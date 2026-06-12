"""Background tasks for keeping series subscribers' calendars in sync (#869).

When an occurrence linked to a series changes, series subscribers must
receive a refreshed (or cancelling) multi-event ``.ics`` so their
calendar stays accurate. The actual per-recipient fan-out lives in
``events.services.series_invite``; these thin task wrappers move the work
onto a worker so the Studio request returns quickly even for a series
with many subscribers, mirroring ``events.tasks.notify_reschedule``.

- ``enqueue_series_update(event_id)`` / ``send_series_update(event_id)`` —
  time change or new-occurrence addition: re-send a ``METHOD:REQUEST``
  series invite to subscribers.
- ``enqueue_series_cancellation(event_id)`` /
  ``send_series_cancellation(event_id)`` — occurrence cancelled: send a
  ``METHOD:CANCEL`` for that occurrence to subscribers.
"""

import logging

from events.models import Event

logger = logging.getLogger(__name__)


def enqueue_series_update(event_id, user_ids=None):
    """Enqueue a series-update fan-out for ``event_id``.

    Called from the Studio reschedule path (``user_ids=None`` — all
    subscribers) and the auto-enroll addition hook (``user_ids`` scoped to
    the newly enrolled subscribers). Fire-and-forget: callers must not let
    an enqueue failure block the originating action.
    """
    from jobs.tasks import async_task, build_task_name

    return async_task(
        'events.tasks.notify_series_invite.send_series_update',
        event_id,
        user_ids,
        task_name=build_task_name(
            'Send series calendar update',
            f'event #{event_id}',
            'series invite sync',
        ),
    )


def send_series_update(event_id, user_ids=None):
    """Worker: re-send the updated series invite to subscribers."""
    try:
        event = Event.objects.get(pk=event_id)
    except Event.DoesNotExist:
        return {'status': 'skipped', 'reason': 'missing_event', 'event_id': event_id}

    from events.services.series_invite import send_series_update_to_subscribers

    count = send_series_update_to_subscribers(event, user_ids=user_ids)
    return {'status': 'sent', 'event_id': event_id, 'count': count}


def enqueue_series_cancellation(event_id):
    """Enqueue a series-cancellation fan-out for ``event_id``."""
    from jobs.tasks import async_task, build_task_name

    return async_task(
        'events.tasks.notify_series_invite.send_series_cancellation',
        event_id,
        task_name=build_task_name(
            'Send series calendar cancellation',
            f'event #{event_id}',
            'series invite sync',
        ),
    )


def send_series_cancellation(event_id):
    """Worker: send a CANCEL for the occurrence to series subscribers."""
    try:
        event = Event.objects.get(pk=event_id)
    except Event.DoesNotExist:
        return {'status': 'skipped', 'reason': 'missing_event', 'event_id': event_id}

    from events.services.series_invite import (
        send_series_cancellation_to_subscribers,
    )

    count = send_series_cancellation_to_subscribers(event)
    return {'status': 'sent', 'event_id': event_id, 'count': count}
