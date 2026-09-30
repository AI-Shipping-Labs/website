"""Who hears about an event: the one shared audience resolver.

Both the recap-ready notice and the automatic pre-event reminders (24h and
20m) mail the same people: the event's registrants plus the members of every
dated cohort whose ``event_series`` is the event's series. The recap notice
also reaches readers of a linked book club. Each small resolver yields
``(user_id, AudienceReason)`` pairs; :func:`resolve_event_audience`
deduplicates them per user, so someone who registered and is also in the
cohort is one recipient with two reasons (and therefore gets one email).

The per-recipient email policy lives here too so every caller suppresses the
same people: an SES complaint, a permanent bounce or an invalid address
always blocks the email. Registrations and cohort enrollments are explicit
sign-ups, so their mail is transactional and a newsletter unsubscribe does
not block it; a recipient with no explicit sign-up (book-club readers) is
suppressed by it.

A cohort member's links point at the course session unit (the syllabus unit
at the event's ``series_position``) with ``?cohort=<key>``, see
:func:`cohort_session_url`.
"""

from collections.abc import Iterator
from dataclasses import dataclass, field
from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db.models import Q

from bookclub.models import Book, ChapterRead, Note
from content.models import CohortEnrollment, Unit
from content.models.cohort import COHORT_MODE_COHORT
from email_app.models import EmailLog, SesEvent
from events.models import EventRegistration
from events.services.calendar_lifecycle import user_has_permanent_bounce
from integrations.config import event_reminders_include_cohort_enabled, site_base_url

User = get_user_model()

REASON_ATTENDED = "attended"
REASON_REGISTERED = "registered"
REASON_COHORT = "cohort"
REASON_BOOK_CLUB = "book_club"

# Reasons that stand for an explicit sign-up to this event or its programme.
# A recipient included only through a softer reason (book-club reading
# activity, which has no explicit sign-up) is treated as a newsletter-style
# audience: the voluntary ``unsubscribed`` flag suppresses their email.
SIGNED_UP_REASONS = frozenset({REASON_ATTENDED, REASON_REGISTERED, REASON_COHORT})


@dataclass(frozen=True)
class AudienceReason:
    """Why one user is in an event audience: a source code and a label."""

    source: str
    label: str = ""

    def as_dict(self):
        return {"source": self.source, "label": self.label}


@dataclass
class AudienceMember:
    """One deduplicated recipient and every reason they were included for."""

    user: object
    reasons: list = field(default_factory=list)

    @property
    def signed_up(self):
        return any(reason.source in SIGNED_UP_REASONS for reason in self.reasons)

    @property
    def via_cohort(self):
        return any(reason.source == REASON_COHORT for reason in self.reasons)


def registrant_reasons(event) -> Iterator[tuple[int, AudienceReason]]:
    """Yield the event's registrants; a joined registrant counts as attended."""
    rows = EventRegistration.objects.filter(event_id=event.pk).values_list(
        "user_id", "joined_at",
    )
    for user_id, joined_at in rows:
        source = REASON_ATTENDED if joined_at else REASON_REGISTERED
        yield user_id, AudienceReason(source)


def _series_cohort_enrollments(event):
    """Enrollments in dated cohorts whose event series is the event's series."""
    return CohortEnrollment.objects.filter(
        cohort__event_series_id=event.event_series_id,
        cohort__mode=COHORT_MODE_COHORT,
    )


def is_series_cohort_member(event, user_id):
    """Whether ``user_id`` is in a dated cohort linked to the event's series."""
    if event.event_series_id is None:
        return False
    return _series_cohort_enrollments(event).filter(user_id=user_id).exists()


def cohort_member_reasons(event) -> Iterator[tuple[int, AudienceReason]]:
    """Yield members of every dated cohort linked to the event's series."""
    if event.event_series_id is None:
        return
    rows = _series_cohort_enrollments(event).values_list(
        "user_id", "cohort__course__title", "cohort__name",
    )
    for user_id, course_title, cohort_name in rows:
        yield user_id, AudienceReason(REASON_COHORT, f"{course_title} - {cohort_name}")


def _books_linked_to_event(event):
    """Return books whose series is the event's series or with a chapter on it."""
    linked = Q(chapters__event_id=event.pk)
    if event.event_series_id is not None:
        linked |= Q(event_series_id=event.event_series_id)
    return Book.objects.filter(linked).distinct().order_by("pk")


def book_club_member_reasons(event) -> Iterator[tuple[int, AudienceReason]]:
    """Yield readers of a linked book club: anyone who marked a chapter read or
    wrote a chapter note. Book clubs have no separate sign-up record, so
    reading activity is the membership signal."""
    for book in _books_linked_to_event(event):
        readers = set(
            ChapterRead.objects.filter(chapter__book=book).values_list("user_id", flat=True),
        )
        readers |= set(
            Note.objects.filter(chapter__book=book).values_list("user_id", flat=True),
        )
        for user_id in sorted(readers):
            yield user_id, AudienceReason(REASON_BOOK_CLUB, book.title)


def resolve_event_audience(event, *, include_cohort=True, include_book_club=True):
    """Return the deduplicated, active audience ordered by user id.

    Registrants are always included. ``include_cohort`` adds members of dated
    cohorts linked to the event's series; ``include_book_club`` adds readers
    of a linked book club.
    """
    resolvers = [registrant_reasons]
    if include_cohort:
        resolvers.append(cohort_member_reasons)
    if include_book_club:
        resolvers.append(book_club_member_reasons)

    reasons_by_user = {}
    for resolver in resolvers:
        for user_id, reason in resolver(event):
            reasons = reasons_by_user.setdefault(user_id, [])
            if reason not in reasons:
                reasons.append(reason)
    users = User.objects.filter(pk__in=reasons_by_user, is_active=True).order_by("pk")
    return [AudienceMember(user, reasons_by_user[user.pk]) for user in users]


def cohort_session_path(event, user_id):
    """Relative URL of the course session unit a cohort member should open.

    A course session's join button, recording and recap live on its syllabus
    session unit, so a member of a dated cohort on the event's series is sent
    to the unit at the event's ``series_position``, with ``?cohort=<key>``.
    Returns ``""`` when the user is not such a cohort member or the course has
    no unit at that position; everyone else keeps the event link.
    """
    if event.event_series_id is None or event.series_position is None:
        return ""
    enrollments = _series_cohort_enrollments(event).filter(
        user_id=user_id,
    ).select_related("cohort").order_by("pk")
    for enrollment in enrollments:
        cohort = enrollment.cohort
        unit = Unit.objects.filter(
            module__course_id=cohort.course_id, kind="event",
            session_position=event.series_position,
        ).order_by("pk").first()
        if unit is None:
            continue
        query = f"?{urlencode({'cohort': cohort.external_key})}" if cohort.external_key else ""
        return f"{unit.get_absolute_url()}{query}"
    return ""


def cohort_session_url(event, user_id):
    """Absolute form of :func:`cohort_session_path`, ``""`` when none."""
    path = cohort_session_path(event, user_id)
    if not path:
        return ""
    return f"{site_base_url().rstrip('/')}{path}"


def user_has_complaint(user):
    """Return whether SES has complained about this user's address.

    Complaints share the legacy ``unsubscribed`` flag with voluntary
    newsletter opt-outs, so that flag alone cannot suppress a transactional
    message without violating the event-registration policy. Keep the
    provider-safety check anchored to the complaint audit fields.
    """
    return (
        EmailLog.objects.filter(
            user_id=user.pk,
            complained_at__isnull=False,
        ).exists()
        or SesEvent.objects.filter(
            user_id=user.pk,
            event_type=SesEvent.EVENT_TYPE_COMPLAINT,
        ).exists()
    )


def email_skip_status(user, signed_up):
    """Return the ``skipped_*`` status that blocks an event email, or ``""``.

    Shared by the recap notice, its dry-run preview and the pre-event
    reminders so all of them suppress the same people. Registration and
    cohort audiences are transactional: a newsletter unsubscribe does not
    suppress them. A recipient with no explicit sign-up (book-club readers)
    is suppressed by it.
    """
    if user_has_complaint(user):
        return "skipped_complaint"
    if user_has_permanent_bounce(user):
        return "skipped_permanent_bounce"
    try:
        validate_email(user.email)
    except ValidationError:
        return "skipped_invalid_address"
    if not signed_up and user.unsubscribed:
        return "skipped_unsubscribed"
    return ""


def viewer_receives_event_reminders(event, user):
    """Whether the 24h/20m reminder emails will reach ``user`` for ``event``.

    Mirrors ``notifications.services.event_reminders``: non-draft,
    non-cancelled events; registrants always, dated cohort members while
    ``EVENT_REMINDERS_INCLUDE_COHORT`` is on; and the same email suppression
    (both reasons are explicit sign-ups).
    """
    if event.status in ("draft", "cancelled"):
        return False
    if not getattr(user, "is_authenticated", False) or not user.is_active:
        return False
    in_audience = EventRegistration.objects.filter(
        event_id=event.pk, user_id=user.pk,
    ).exists()
    if not in_audience and event_reminders_include_cohort_enabled():
        in_audience = is_series_cohort_member(event, user.pk)
    if not in_audience:
        return False
    return email_skip_status(user, signed_up=True) == ""
