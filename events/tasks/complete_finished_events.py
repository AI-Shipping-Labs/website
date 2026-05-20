"""Periodic task that transitions finished events from `upcoming` to `completed`.

Runs every 5 minutes via Django-Q (registered in
`jobs.management.commands.setup_schedules`). Events keep `status` as the
source of truth for `is_upcoming` / `is_past` and all dependent surfaces
(listings, dashboard widgets, registration API, recap branching). When an
event's effective end is in the past but no human or webhook has flipped
its status, this job does it.

Effective end time:
- `end_datetime` when it is set.
- `start_datetime + 1 hour` when `end_datetime` is null. Mirrors the
  `.ics` fallback in `events/services/calendar_invite.py` so an event
  without an explicit end is treated as "ended" exactly 1 hour after it
  starts.

The job is idempotent: it only updates rows whose `status` is `upcoming`,
so a second pass over the same data is a no-op.

Issue #680: after the status flip, re-query each just-completed event and
enqueue the post-event follow-up fan-out when (a) the event has a
recording URL set AND (b) no ``EventReminderLog(interval='followup')``
row exists yet. Events without a recording URL are skipped — the
Studio "Send follow-up now" button is the documented escape hatch.
"""

import logging
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from events.tasks.send_post_event_followup import (
    INTERVAL_FOLLOWUP,
    enqueue_post_event_followup,
)

logger = logging.getLogger(__name__)


def complete_finished_events():
    """Flip `status` from `upcoming` to `completed` for finished events.

    Returns:
        int: total number of rows updated across both branches.
    """
    from events.models import Event

    now = timezone.now()

    explicit_end_filter = Q(
        status='upcoming',
        end_datetime__lte=now,
    )
    implicit_end_filter = Q(
        status='upcoming',
        end_datetime__isnull=True,
        start_datetime__lte=now - timedelta(hours=1),
    )

    # Issue #680: capture the PKs of the rows that are about to flip
    # so we can enqueue post-event follow-ups for them. We resolve the
    # PKs BEFORE the .update() so the followup-eligible set matches the
    # rows we just transitioned (an .update() returns a count, not a
    # queryset of touched rows).
    pre_flip_pks = list(
        Event.objects
        .filter(explicit_end_filter | implicit_end_filter)
        .values_list('pk', flat=True),
    )

    explicit_end_flipped = Event.objects.filter(explicit_end_filter).update(
        status='completed',
    )
    implicit_end_flipped = Event.objects.filter(implicit_end_filter).update(
        status='completed',
    )

    total = explicit_end_flipped + implicit_end_flipped

    if total:
        logger.info(
            'Flipped %d event(s) from upcoming to completed', total,
        )

    if pre_flip_pks:
        _maybe_enqueue_post_event_followups(pre_flip_pks)

    return total


def _maybe_enqueue_post_event_followups(event_pks):
    """Issue #680: enqueue post-event follow-up for eligible just-flipped events.

    Eligibility (all must hold):

    - Event has a recording URL set (``recording_s3_url`` OR
      ``recording_url`` non-empty).
    - No existing ``EventReminderLog(event, interval='followup')`` row
      — protects against a re-run of the cron pushing duplicate
      follow-ups when the cron-detected set overlaps with a manual
      "Send follow-up now" press.

    The cron's "did we already send a followup for this event?" check
    is the existence query on the log; if any rows exist, we skip the
    whole event. The per-user idempotency gate inside
    ``send_post_event_followup_one`` is the second layer of defence.
    """
    from events.models import Event
    from notifications.models import EventReminderLog

    events_with_recording = (
        Event.objects
        .filter(pk__in=event_pks)
        .exclude(recording_s3_url='', recording_url='')
    )

    already_sent_pks = set(
        EventReminderLog.objects
        .filter(event_id__in=event_pks, interval=INTERVAL_FOLLOWUP)
        .values_list('event_id', flat=True)
        .distinct(),
    )

    enqueued = 0
    for event in events_with_recording:
        if event.pk in already_sent_pks:
            continue
        try:
            enqueue_post_event_followup(event.pk)
            enqueued += 1
        except Exception:
            logger.exception(
                'Failed to enqueue post-event follow-up for event %s',
                event.pk,
            )

    if enqueued:
        logger.info(
            'Enqueued post-event follow-up fan-out for %d just-completed event(s)',
            enqueued,
        )
