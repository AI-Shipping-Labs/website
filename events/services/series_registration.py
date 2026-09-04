"""Series-registration fan-out helpers (issue #857).

A ``SeriesRegistration`` is a standing flag. Registering for the series
fans the flag out into real per-event ``EventRegistration`` rows for
every eligible upcoming occurrence, so every existing per-event surface
(dashboard, reminders, follow-ups, ``.ics``) keeps working with
no changes. Occurrences added later auto-enroll existing registrants via
``enroll_series_registrants_in_event``.

Eligibility for the fan-out (an occurrence is enrolled only when ALL of
these hold):

- ``is_upcoming`` — future, non-draft, non-cancelled.
- The user ``can_access`` it (tier). Inaccessible occurrences are counted
  in ``skipped_no_access`` rather than blocking the whole action.
- The user is not already registered (counted in ``skipped_already``).
- The user has not deliberately opted out of that session (issue #1460 —
  counted in ``skipped_opted_out``). A ``SeriesOccurrenceOptOut`` is
  never overridden by a later fan-out.
"""

import logging

from django.db import transaction

from content.access import can_access
from events.models import (
    EventRegistration,
    SeriesOccurrenceOptOut,
    SeriesRegistration,
)
from events.services.registration import (
    get_or_create_event_registration,
    get_or_create_series_registration,
)

logger = logging.getLogger(__name__)


def _eligible_occurrences(series):
    """Return upcoming, non-draft, non-cancelled occurrences of ``series``.

    ``is_upcoming`` is a time-derived property (issue #713) so we filter
    in Python after pulling the candidate rows. We pre-exclude draft and
    cancelled rows in SQL to keep the candidate set small.
    """
    candidates = series.events.exclude(
        status__in=('draft', 'cancelled'),
    )
    return [event for event in candidates if event.is_upcoming]


def _opted_out_event_ids(user, occurrences):
    """Return the ids of ``occurrences`` this user deliberately skipped."""
    if not occurrences:
        return set()
    return set(
        SeriesOccurrenceOptOut.objects.filter(
            user=user, event__in=occurrences,
        ).values_list('event_id', flat=True)
    )


def enroll_user_in_series(user, series):
    """Fan a series registration out into per-event registrations.

    Creates an ``EventRegistration`` for every eligible upcoming
    occurrence the ``user`` can access. Idempotent:
    occurrences the user is already registered for are skipped, not
    duplicated.

    The caller is responsible for creating the ``SeriesRegistration``
    standing flag; this helper only performs the fan-out so it can be
    reused by both the register endpoint and (best-effort) by the
    occurrence-creation auto-enroll path.

    Returns a structured summary::

        {
            'registered': int,        # new EventRegistration rows created
            'skipped_already': int,   # already registered for the occurrence
            'skipped_no_access': int, # tier too low
            'skipped_opted_out': int, # deliberately cancelled session
            'total_occurrences': int, # eligible upcoming occurrences seen
        }
    """
    summary = {
        'registered': 0,
        'skipped_already': 0,
        'skipped_no_access': 0,
        'skipped_opted_out': 0,
        'total_occurrences': 0,
    }

    occurrences = _eligible_occurrences(series)
    summary['total_occurrences'] = len(occurrences)

    with transaction.atomic():
        already_registered_ids = set(
            EventRegistration.objects.filter(
                user=user, event__in=occurrences,
            ).values_list('event_id', flat=True)
        )
        opted_out_ids = _opted_out_event_ids(user, occurrences)

        new_events = []
        for event in occurrences:
            if event.id in already_registered_ids:
                summary['skipped_already'] += 1
                continue
            # Issue #1460: a deliberately cancelled session is never silently
            # re-registered by a later fan-out.
            if event.id in opted_out_ids:
                summary['skipped_opted_out'] += 1
                continue
            if not can_access(user, event):
                summary['skipped_no_access'] += 1
                continue
            _, created = get_or_create_event_registration(event, user)
            if created:
                new_events.append(event)
                summary['registered'] += 1
            else:
                summary['skipped_already'] += 1

    # ``new_events`` is the list of ``Event`` rows that newly got an
    # ``EventRegistration`` — the summary email iterates these to build
    # the chronological occurrence list.
    summary['new_events'] = new_events
    return summary


def clear_series_opt_outs(user, series):
    """Drop every opt-out ``user`` holds for ``series`` (clean slate).

    Called when the user re-registers for the whole series and when their
    ``SeriesRegistration`` is deleted, so a later registration starts from
    the default "every session" intent rather than resurrecting stale
    per-session cancellations.
    """
    deleted, _ = SeriesOccurrenceOptOut.objects.filter(
        user=user, series=series,
    ).delete()
    return deleted


def record_series_opt_out(user, event):
    """Record that ``user`` is skipping this one session of its series.

    Only meaningful for a user who holds the standing
    ``SeriesRegistration`` for the occurrence's series: without the flag
    there is nothing that would re-register them, so no row is written.

    Returns ``True`` when the opt-out exists after the call (whether it
    was created now or already present), ``False`` otherwise.
    """
    series_id = event.event_series_id
    if series_id is None:
        return False
    if not SeriesRegistration.objects.filter(
        series_id=series_id, user=user,
    ).exists():
        return False

    SeriesOccurrenceOptOut.objects.get_or_create(
        event=event, user=user, defaults={'series_id': series_id},
    )
    return True


def register_occurrence_with_series(user, event):
    """Register ``user`` for ``event`` AND the rest of its series (#1460).

    This is the default scope of the per-occurrence register endpoint:
    signing up for one session of a series expresses the standing intent
    to attend the whole series. It creates the ``SeriesRegistration`` flag
    (idempotent) and then runs the ordinary fan-out, which also creates
    the ``EventRegistration`` row for ``event`` itself — deliberately, so
    this occurrence lands in ``new_events`` and the series calendar invite
    covers it.

    Any opt-out on ``event`` is cleared first: clicking Register on a
    session you previously cancelled is an explicit request for it back.

    Returns the fan-out summary (including ``new_events``), or ``None``
    when the event does not belong to a series.
    """
    series = event.event_series
    if series is None:
        return None

    with transaction.atomic():
        SeriesOccurrenceOptOut.objects.filter(event=event, user=user).delete()
        # A concurrent collision here means the standing flag already
        # exists; keep going and fan out the remaining occurrences.
        get_or_create_series_registration(series, user)
        return enroll_user_in_series(user, series)


def series_registration_summary(user, series):
    """Return the current fan-out summary without creating any rows.

    Used by the idempotent re-register path: an already-series-registered
    user POSTing again should see the same shape of summary the original
    fan-out produced, computed from the live state. Occurrences the user
    is already registered for count as ``skipped_already`` (the standard
    "already covered" bucket); the remaining buckets mirror
    ``enroll_user_in_series``. ``registered`` is always 0 here because
    this helper never writes.
    """
    summary = {
        'registered': 0,
        'skipped_already': 0,
        'skipped_no_access': 0,
        'skipped_opted_out': 0,
        'total_occurrences': 0,
    }

    occurrences = _eligible_occurrences(series)
    summary['total_occurrences'] = len(occurrences)

    already_registered_ids = set(
        EventRegistration.objects.filter(
            user=user, event__in=occurrences,
        ).values_list('event_id', flat=True)
    )
    opted_out_ids = _opted_out_event_ids(user, occurrences)

    for event in occurrences:
        if event.id in already_registered_ids:
            summary['skipped_already'] += 1
        elif event.id in opted_out_ids:
            summary['skipped_opted_out'] += 1
        elif not can_access(user, event):
            summary['skipped_no_access'] += 1
        else:
            summary['skipped_already'] += 1
    return summary


def promote_event_registrations_to_series(event):
    """Promote a single event's registrants to standing series registrants.

    For every user registered for ``event``, create the ``SeriesRegistration``
    standing flag on the event's series (idempotent) and fan it out into real
    per-event ``EventRegistration`` rows across the series' eligible upcoming
    occurrences. The source event's own registrations are left intact — this
    widens each signup to the whole series so sibling occurrences (e.g. later
    sessions in the same book club / cohort) share the same audience, and any
    occurrence added later auto-enrolls them too.

    Returns a structured summary::

        {
            'series_slug': str,
            'registrants': int,       # users registered for the source event
            'new_series_flags': int,  # SeriesRegistration rows created now
            'already_series': int,    # users already series-registered
            'fanned_out': int,        # new EventRegistration rows created
        }

    Raises ``ValueError`` if the event is not linked to a series.
    """
    from events.models import EventRegistration, SeriesRegistration

    series = event.event_series
    if series is None:
        raise ValueError('Event is not linked to a series.')

    user_ids = list(
        EventRegistration.objects.filter(event=event)
        .values_list('user_id', flat=True)
    )
    summary = {
        'series_slug': series.slug,
        'registrants': len(user_ids),
        'new_series_flags': 0,
        'already_series': 0,
        'fanned_out': 0,
    }
    if not user_ids:
        return summary

    from accounts.models import User

    for user in User.objects.filter(id__in=user_ids):
        _, created = SeriesRegistration.objects.get_or_create(
            series=series, user=user,
        )
        if created:
            summary['new_series_flags'] += 1
        else:
            summary['already_series'] += 1
        fan = enroll_user_in_series(user, series)
        summary['fanned_out'] += fan['registered']
    return summary


class EnrollmentCount(int):
    """The enrolled-user count, carrying the skipped-opt-out bucket.

    ``enroll_series_registrants_in_event`` has always returned the number
    of users it enrolled, and its callers (Studio, the API, the
    publication lifecycle) treat the result as a plain integer. Issue
    #1460 adds a second bucket — users who deliberately opted out of this
    session — that the caller may want to surface without changing the
    established return contract, so the count is an ``int`` subclass with
    ``skipped_opted_out`` attached.
    """

    skipped_opted_out = 0

    def __new__(cls, value, skipped_opted_out=0):
        obj = super().__new__(cls, value)
        obj.skipped_opted_out = skipped_opted_out
        return obj


def enroll_series_registrants_in_event(event):
    """Auto-enroll existing series registrants into a new occurrence.

    Called from the three occurrence-creation paths (Studio create,
    Studio add-occurrence, API bulk) whenever an occurrence is linked to
    a series. Best-effort: a failure here must never block occurrence
    creation, so the whole body is wrapped and logged.

    Respects the same eligibility rules as ``enroll_user_in_series`` for
    the single new occurrence: only enroll registrants who can access it
    and only when it is upcoming.
    """
    series = event.event_series
    if series is None:
        return EnrollmentCount(0)

    try:
        if not event.is_upcoming:
            return EnrollmentCount(0)

        registrant_user_ids = (
            series.series_registrations.values_list('user_id', flat=True)
        )
        if not registrant_user_ids:
            return EnrollmentCount(0)

        from accounts.models import User

        already_registered_ids = set(
            EventRegistration.objects.filter(
                event=event, user_id__in=registrant_user_ids,
            ).values_list('user_id', flat=True)
        )
        # Issue #1460: users who deliberately cancelled this session keep
        # their standing series flag but must never be re-enrolled here.
        opted_out_user_ids = set(
            SeriesOccurrenceOptOut.objects.filter(
                event=event, user_id__in=registrant_user_ids,
            ).values_list('user_id', flat=True)
        )

        enrolled = 0
        skipped_opted_out = 0
        enrolled_user_ids = []
        users = User.objects.filter(id__in=registrant_user_ids)
        with transaction.atomic():
            for user in users:
                if user.id in already_registered_ids:
                    continue
                if user.id in opted_out_user_ids:
                    skipped_opted_out += 1
                    continue
                if not can_access(user, event):
                    continue
                _, created = get_or_create_event_registration(event, user)
                if not created:
                    continue
                enrolled += 1
                enrolled_user_ids.append(user.id)

        # Issue #869: subscribers auto-enrolled into a newly added/published
        # occurrence get an updated series invite covering the new session.
        # Fire-and-forget; an enqueue failure must not block enrollment.
        if enrolled_user_ids:
            try:
                from events.tasks.notify_series_invite import (
                    enqueue_series_update,
                )
                enqueue_series_update(event.pk, enrolled_user_ids)
            except Exception:
                logger.exception(
                    'Failed to enqueue series update after auto-enroll for '
                    'event "%s"',
                    getattr(event, 'slug', '?'),
                )
        return EnrollmentCount(enrolled, skipped_opted_out)
    except Exception:
        logger.exception(
            'Failed to auto-enroll series registrants for event "%s"',
            getattr(event, 'slug', '?'),
        )
        return EnrollmentCount(0)
