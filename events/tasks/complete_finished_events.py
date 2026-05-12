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
"""

import logging
from datetime import timedelta

from django.utils import timezone

logger = logging.getLogger(__name__)


def complete_finished_events():
    """Flip `status` from `upcoming` to `completed` for finished events.

    Returns:
        int: total number of rows updated across both branches.
    """
    from events.models import Event

    now = timezone.now()

    explicit_end_flipped = Event.objects.filter(
        status='upcoming',
        end_datetime__lte=now,
    ).update(status='completed')

    implicit_end_flipped = Event.objects.filter(
        status='upcoming',
        end_datetime__isnull=True,
        start_datetime__lte=now - timedelta(hours=1),
    ).update(status='completed')

    total = explicit_end_flipped + implicit_end_flipped

    if total:
        logger.info(
            'Flipped %d event(s) from upcoming to completed', total,
        )

    return total
