"""Series-registration fan-out helpers (issue #857).

A ``SeriesRegistration`` is a standing flag. Registering for the series
fans the flag out into real per-event ``EventRegistration`` rows for
every eligible upcoming occurrence, so every existing per-event surface
(dashboard, reminders, follow-ups, capacity, ``.ics``) keeps working with
no changes. Occurrences added later auto-enroll existing registrants via
``enroll_series_registrants_in_event``.

Eligibility for the fan-out (an occurrence is enrolled only when ALL of
these hold):

- ``is_upcoming`` — future, non-draft, non-cancelled.
- The user ``can_access`` it (tier). Inaccessible occurrences are counted
  in ``skipped_no_access`` rather than blocking the whole action.
- It is not ``is_full``. Full occurrences are counted in ``skipped_full``.
- The user is not already registered (counted in ``skipped_already``).
"""

import logging

from content.access import can_access
from events.models import EventRegistration

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


def enroll_user_in_series(user, series):
    """Fan a series registration out into per-event registrations.

    Creates an ``EventRegistration`` for every eligible upcoming
    occurrence the ``user`` can access and that is not full. Idempotent:
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
            'skipped_full': int,      # at capacity
            'total_occurrences': int, # eligible upcoming occurrences seen
        }
    """
    summary = {
        'registered': 0,
        'skipped_already': 0,
        'skipped_no_access': 0,
        'skipped_full': 0,
        'total_occurrences': 0,
    }

    occurrences = _eligible_occurrences(series)
    summary['total_occurrences'] = len(occurrences)

    already_registered_ids = set(
        EventRegistration.objects.filter(
            user=user, event__in=occurrences,
        ).values_list('event_id', flat=True)
    )

    new_events = []
    for event in occurrences:
        if event.id in already_registered_ids:
            summary['skipped_already'] += 1
            continue
        if not can_access(user, event):
            summary['skipped_no_access'] += 1
            continue
        if event.is_full:
            summary['skipped_full'] += 1
            continue
        EventRegistration.objects.create(event=event, user=user)
        from analytics.activity import record_event_register
        record_event_register(user, event)
        new_events.append(event)
        summary['registered'] += 1

    # ``new_events`` is the list of ``Event`` rows that newly got an
    # ``EventRegistration`` — the summary email iterates these to build
    # the chronological occurrence list.
    summary['new_events'] = new_events
    return summary


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
        'skipped_full': 0,
        'total_occurrences': 0,
    }

    occurrences = _eligible_occurrences(series)
    summary['total_occurrences'] = len(occurrences)

    already_registered_ids = set(
        EventRegistration.objects.filter(
            user=user, event__in=occurrences,
        ).values_list('event_id', flat=True)
    )

    for event in occurrences:
        if event.id in already_registered_ids:
            summary['skipped_already'] += 1
        elif not can_access(user, event):
            summary['skipped_no_access'] += 1
        elif event.is_full:
            summary['skipped_full'] += 1
        else:
            summary['skipped_already'] += 1
    return summary


def enroll_series_registrants_in_event(event):
    """Auto-enroll existing series registrants into a new occurrence.

    Called from the three occurrence-creation paths (Studio create,
    Studio add-occurrence, API bulk) whenever an occurrence is linked to
    a series. Best-effort: a failure here must never block occurrence
    creation, so the whole body is wrapped and logged.

    Respects the same eligibility rules as ``enroll_user_in_series`` for
    the single new occurrence: only enroll registrants who can access it
    and only when it is upcoming and not full.
    """
    series = event.event_series
    if series is None:
        return 0

    try:
        if not event.is_upcoming:
            return 0

        registrant_user_ids = (
            series.series_registrations.values_list('user_id', flat=True)
        )
        if not registrant_user_ids:
            return 0

        from accounts.models import User

        already_registered_ids = set(
            EventRegistration.objects.filter(
                event=event, user_id__in=registrant_user_ids,
            ).values_list('user_id', flat=True)
        )

        enrolled = 0
        enrolled_user_ids = []
        users = User.objects.filter(id__in=registrant_user_ids)
        for user in users:
            if user.id in already_registered_ids:
                continue
            if not can_access(user, event):
                continue
            if event.is_full:
                # Capacity can be reached mid-loop; recheck each time.
                break
            EventRegistration.objects.create(event=event, user=user)
            from analytics.activity import record_event_register
            record_event_register(user, event)
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
        return enrolled
    except Exception:
        logger.exception(
            'Failed to auto-enroll series registrants for event "%s"',
            getattr(event, 'slug', '?'),
        )
        return 0
