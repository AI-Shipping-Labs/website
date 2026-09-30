"""Resolve Maven course/cohort keys and match occurrences across key changes.

Maven renamed its payload identifiers mid-cohort: enrollments of cohort 4
arrived as ``course_key='ai engineering buildcamp: from rag to agents'`` /
``cohort_key='4/11'`` and the removals as ``from-rag-to-agents`` / ``4``.
Those produce different ``identity_hash`` values, so a removal that only
looks up its own hash misses the active enrollment and records a separate
removed row, leaving the student enrolled. This module owns the looser,
resolution-based "is this the same enrollment?" rule that removal intake,
the removal re-apply repair, and the split-pair diagnostics share.
"""

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.text import slugify

from accounts.services.email_resolution import normalize_email
from community.models import CommunityAuditLog
from content.models import Course
from content.models.cohort import Cohort
from integrations.models import MavenEnrollmentEvent

EVENT_REMOVED = "user_cohort.removed"
SPLIT_REPAIR_AUDIT_ACTION = "maven_occurrence_split_repaired"
REMOVAL_MATCH_AUDIT_ACTION = "maven_removal_matched_by_resolution"


class MavenUnknownCourseError(Exception):
    """No ``Course.maven_course_key`` matches the occurrence's ``course_key``."""


class MavenUnknownCohortError(Exception):
    """No ``Cohort.external_key`` under the resolved course matches ``cohort_key``."""


def resolve_maven_course(course_key):
    """Resolve ``Course.maven_course_key`` case-insensitively, or raise.

    A blank ``course_key`` is always unresolvable — matching it against
    blank ``maven_course_key`` rows (the common unconfigured default) would
    silently grant the wrong course, so it is excluded explicitly rather
    than relying on an empty-string match to fail to find anything.
    """
    if not course_key:
        raise MavenUnknownCourseError("course_key is blank")
    course = (
        Course.objects.exclude(aisl_extension__maven_course_key="")
        .filter(aisl_extension__maven_course_key__iexact=course_key)
        .first()
    )
    if course is None:
        raise MavenUnknownCourseError(
            f"no Course.maven_course_key matches {course_key!r}"
        )
    return course


def resolve_maven_cohort(course, cohort_key):
    """Resolve ``Cohort.external_key`` under ``course`` case-insensitively, or raise."""
    if not cohort_key:
        raise MavenUnknownCohortError("cohort_key is blank")
    cohort = (
        Cohort.objects.filter(course=course)
        .exclude(external_key="")
        .filter(external_key__iexact=cohort_key)
        .first()
    )
    if cohort is None:
        raise MavenUnknownCohortError(
            f"no Cohort.external_key under course={course.pk} matches {cohort_key!r}"
        )
    return cohort


def _text_tokens(values):
    """Lower-cased raw and slugified forms of every non-blank value."""
    tokens = set()
    for value in values:
        text = str(value or "").strip().lower()
        if text:
            tokens.add(text)
            tokens.add(slugify(text))
    tokens.discard("")
    return tokens


def _course_for_tokens(tokens):
    """The single Course whose Maven key or title equals one of ``tokens``."""
    if not tokens:
        return None
    match = Q()
    for token in tokens:
        match |= Q(aisl_extension__maven_course_key__iexact=token)
        match |= Q(title__iexact=token)
    courses = list(
        Course.objects.filter(match).exclude(aisl_extension__maven_course_key="")[:2]
    )
    if len(courses) != 1:
        return None
    return courses[0]


def _cohort_tokens(values):
    """Cohort tokens; ``4/11`` also yields ``4`` (Maven's two cohort labels)."""
    tokens = _text_tokens(values)
    for token in list(tokens):
        head = token.split("/", 1)[0].strip()
        if head:
            tokens.add(head)
    return tokens


def enrollment_signature(course_key, cohort_key, course="", cohort=""):
    """Return ``(course_tokens, cohort_tokens)`` for one Maven enrollment.

    Tokens are the normalized raw keys and labels plus, when they resolve,
    the ``course:<pk>`` / ``cohort:<pk>`` of the linked content rows. Two
    enrollments are the same when both token sets intersect.
    """
    course_tokens = _text_tokens((course_key, course))
    cohort_tokens = _cohort_tokens((cohort_key, cohort))
    resolved = _course_for_tokens(course_tokens)
    if resolved is not None:
        course_tokens.add(f"course:{resolved.pk}")
        for pk in _cohort_pks(resolved, cohort_tokens):
            cohort_tokens.add(f"cohort:{pk}")
    return frozenset(course_tokens), frozenset(cohort_tokens)


def _cohort_pks(course, tokens):
    if not tokens:
        return []
    match = Q()
    for token in tokens:
        match |= Q(external_key__iexact=token)
    cohorts = Cohort.objects.filter(course=course).exclude(external_key="")
    return list(cohorts.filter(match).values_list("pk", flat=True))


def occurrence_signature(occurrence):
    return enrollment_signature(
        occurrence.course_key, occurrence.cohort_key,
        occurrence.course, occurrence.cohort,
    )


def same_enrollment(left, right):
    return bool(left[0] & right[0]) and bool(left[1] & right[1])


def _same_person(user_id, email):
    """Rows owned by the resolved account, or by the normalized email."""
    match = Q()
    if user_id is not None:
        match |= Q(user_id=user_id)
    if email:
        match |= Q(email__iexact=normalize_email(email))
    return match


def matching_active_occurrences(user_id, email, signature, *, before):
    """Active occurrences of the same person and enrollment created before ``before``."""
    person = _same_person(user_id, email)
    if not person:
        return []
    candidates = (
        MavenEnrollmentEvent.objects.select_for_update()
        .filter(person, lifecycle=MavenEnrollmentEvent.LIFECYCLE_ACTIVE, created_at__lt=before)
        .order_by("-created_at", "-pk")
    )
    return [row for row in candidates if same_enrollment(occurrence_signature(row), signature)]


def claim_resolution_match(user_id, email, keys, identity_hash, *, now):
    """Return the active occurrence a removal with different raw keys closes.

    Maven changed its identifiers between enrollment and removal (title vs
    slug, ``4/11`` vs ``4``), so the hashes differ. Match the same person
    plus the same resolved course and cohort instead, and move the row onto
    the removal's keys so the removal step resolves the course it revokes.
    ``keys`` is ``(course_key, cohort_key, course_label, cohort_label)``.
    """
    matches = matching_active_occurrences(user_id, email, enrollment_signature(*keys), before=now)
    if not matches:
        return None
    occurrence = matches[0]
    old_keys = (occurrence.course_key, occurrence.cohort_key)
    if _course_key_resolves(keys[0]):
        occurrence.course_key, occurrence.cohort_key = keys[0], keys[1]
    # The hash always follows the removal, so a redelivery finds this row.
    occurrence.identity_hash = identity_hash
    occurrence.save(update_fields=["course_key", "cohort_key", "identity_hash", "updated_at"])
    CommunityAuditLog.objects.create(
        user=occurrence.user,
        action=REMOVAL_MATCH_AUDIT_ACTION,
        details=(
            f"occurrence={occurrence.pk} course_key={old_keys[0]!r}->{occurrence.course_key!r} "
            f"cohort_key={old_keys[1]!r}->{occurrence.cohort_key!r}"
        ),
    )
    return occurrence


def _course_key_resolves(course_key):
    try:
        resolve_maven_course(course_key)
    except MavenUnknownCourseError:
        return False
    return True


def split_siblings(removed):
    """Active occurrences a removed occurrence failed to close at intake."""
    before = removed.removed_at or removed.created_at
    siblings = matching_active_occurrences(
        removed.user_id, removed.email, occurrence_signature(removed), before=before,
    )
    return [row for row in siblings if row.pk != removed.pk]


def close_split_siblings(removed, actions):
    """Mark every split sibling removed, with one audit row each; return them.

    The removal work itself (course access, cohort, tags, override) runs on
    ``removed``, so each sibling's own ``removal`` step is ``skipped``.
    """
    with transaction.atomic():
        siblings = split_siblings(removed)
        for sibling in siblings:
            _close_sibling(sibling, removed, actions)
    return siblings


def _close_sibling(sibling, removed, actions):
    sibling.lifecycle = MavenEnrollmentEvent.LIFECYCLE_REMOVED
    sibling.removed_at = removed.removed_at or timezone.now()
    sibling.event_type = EVENT_REMOVED
    sibling.outcome = MavenEnrollmentEvent.OUTCOME_REMOVAL_NOTIFIED
    sibling.removal_status = MavenEnrollmentEvent.STEP_SKIPPED
    sibling.save(
        update_fields=["lifecycle", "removed_at", "event_type", "outcome", "removal_status", "updated_at"],
    )
    CommunityAuditLog.objects.create(
        user=sibling.user,
        action=SPLIT_REPAIR_AUDIT_ACTION,
        details=f"occurrence={sibling.pk} closed as the active sibling of removed occurrence={removed.pk}",
    )
    actions.append(f"Closed split active occurrence {sibling.pk}.")


def split_pairs():
    """Return ``(active, removed)`` pairs where a later removal missed the enrollment.

    Read-only diagnostics: each pair is a removal recorded as its own row
    while the same person's earlier enrollment of the same cohort stayed
    ``active``. ``/removal/reapply`` on the removed row repairs the pair.
    """
    active_rows = list(
        MavenEnrollmentEvent.objects.select_related("user")
        .filter(lifecycle=MavenEnrollmentEvent.LIFECYCLE_ACTIVE)
        .order_by("created_at", "pk")
    )
    removed_rows = (
        MavenEnrollmentEvent.objects.select_related("user")
        .filter(lifecycle=MavenEnrollmentEvent.LIFECYCLE_REMOVED)
        .order_by("created_at", "pk")
    )
    signatures = {}
    pairs = []
    for removed in removed_rows:
        for active in _earlier_active_of_same_person(active_rows, removed):
            if same_enrollment(_cached_signature(signatures, active), _cached_signature(signatures, removed)):
                pairs.append((active, removed))
    return pairs


def _earlier_active_of_same_person(active_rows, removed):
    before = removed.removed_at or removed.created_at
    earlier = []
    for row in active_rows:
        if row.created_at < before and _is_same_person(row, removed):
            earlier.append(row)
    return earlier


def _cached_signature(cache, occurrence):
    if occurrence.pk not in cache:
        cache[occurrence.pk] = occurrence_signature(occurrence)
    return cache[occurrence.pk]


def _is_same_person(left, right):
    if left.user_id is not None and left.user_id == right.user_id:
        return True
    return bool(left.email) and normalize_email(left.email) == normalize_email(right.email)
