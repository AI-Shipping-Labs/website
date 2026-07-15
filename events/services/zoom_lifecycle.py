"""Fail-soft Zoom lifecycle helpers for saved Event rows."""

import logging

from django.utils import timezone

from integrations.services.zoom import (
    ZoomAPIError,
    build_meeting_payload,
    delete_meeting,
    update_meeting,
)

logger = logging.getLogger(__name__)

_SYNC_KEYS = ('topic', 'start_time', 'duration', 'timezone')


def _is_zoom_backed(event):
    return event.platform == 'zoom' and bool(event.zoom_meeting_id)


def _schedule_signature(event):
    payload = build_meeting_payload(event, include_type=False)
    return tuple(payload[key] for key in _SYNC_KEYS)


def should_sync_zoom_meeting(event, old_event):
    """Return whether a saved active event needs an in-place Zoom PATCH."""
    if old_event is None:
        return False
    if not _is_zoom_backed(event):
        return False
    if event.status == 'cancelled':
        return False
    return _schedule_signature(event) != _schedule_signature(old_event)


def maybe_sync_zoom_meeting(event, old_event):
    """Patch a Zoom meeting in place when title/schedule/timezone changed.

    Returns ``None`` on success/no-op, or a non-fatal error string for caller
    surfaces. The platform Event save is never rolled back by Zoom failures.
    """
    if not should_sync_zoom_meeting(event, old_event):
        return None

    try:
        update_meeting(event)
    except ZoomAPIError as exc:
        logger.exception(
            'zoom_sync: failed to patch meeting %s for event %s (%s)',
            event.zoom_meeting_id, event.pk, event.slug,
        )
        return str(exc)
    except Exception as exc:  # noqa: BLE001 - fail soft by contract
        logger.exception(
            'zoom_sync: unexpected error patching meeting %s for event %s (%s)',
            event.zoom_meeting_id, event.pk, event.slug,
        )
        return str(exc) or 'Failed to update Zoom meeting.'
    return None


def should_delete_zoom_meeting_on_cancel(event, old_status):
    """Return whether a cancellation should delete the external Zoom meeting."""
    if event.status != 'cancelled' or old_status == 'cancelled':
        return False
    if not _is_zoom_backed(event):
        return False
    if event.start_datetime is None:
        return False
    return event.start_datetime > timezone.now()


def maybe_delete_zoom_meeting_for_cancellation(event, old_status):
    """Delete a future cancelled event's Zoom meeting and clear local fields."""
    if not should_delete_zoom_meeting_on_cancel(event, old_status):
        return None

    meeting_id = event.zoom_meeting_id
    try:
        delete_meeting(event)
    except ZoomAPIError as exc:
        logger.exception(
            'zoom_cancel: failed to delete meeting %s for event %s (%s)',
            meeting_id, event.pk, event.slug,
        )
        return str(exc)
    except Exception as exc:  # noqa: BLE001 - fail soft by contract
        logger.exception(
            'zoom_cancel: unexpected error deleting meeting %s for event %s (%s)',
            meeting_id, event.pk, event.slug,
        )
        return str(exc) or 'Failed to delete Zoom meeting.'

    event.zoom_meeting_id = ''
    event.zoom_join_url = ''
    event.save(update_fields=['zoom_meeting_id', 'zoom_join_url'])
    return None


def sync_or_delete_zoom_meeting(event, old_event):
    """Apply the relevant Zoom lifecycle action after an Event save."""
    zoom_error = maybe_delete_zoom_meeting_for_cancellation(
        event,
        old_event.status if old_event is not None else None,
    )
    if zoom_error is not None:
        return zoom_error
    return maybe_sync_zoom_meeting(event, old_event)


def sync_changed_zoom_occurrences(changes, *, skip_event_ids=()):
    """Synchronize indirectly retitled series occurrences after commit.

    ``changes`` contains ``(saved_event, old_event)`` pairs captured while a
    series transaction renumbered auto-titled occurrences. Provider calls must
    happen after that transaction, matching the fail-soft standalone lifecycle
    contract. Event ids are de-duplicated so a direct occurrence PATCH can sync
    its own schedule/title once and ask this helper to handle only siblings.

    Returns API-ready per-occurrence error rows. Successful/no-op rows are not
    reported; local meeting identity is preserved by the lifecycle helper on
    provider failure.
    """
    skipped = set(skip_event_ids)
    unique_changes = {}
    for event, old_event in changes:
        if event.pk in skipped:
            continue
        unique_changes[event.pk] = (event, old_event)

    errors = []
    for event, old_event in unique_changes.values():
        zoom_error = sync_or_delete_zoom_meeting(event, old_event)
        if zoom_error is not None:
            errors.append({'event_id': event.pk, 'zoom_error': zoom_error})
    return errors
