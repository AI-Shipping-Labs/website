"""Fail-soft Zoom lifecycle helpers for saved Event rows."""

import logging

from django.utils import timezone

from integrations.services.zoom import (
    ZoomAPIError,
    build_meeting_payload,
    delete_meeting,
    sanitize_provider_message,
    update_meeting,
)

logger = logging.getLogger(__name__)

_SYNC_KEYS = ('topic', 'start_time', 'duration', 'timezone')


class ZoomSyncFailure(dict):
    """JSON-safe Zoom failure that renders as a concise Studio warning."""

    def __str__(self):
        return self['message']


def _failure_details(exc, *, operation):
    if isinstance(exc, ZoomAPIError):
        details = exc.diagnostics(operation)
        if 'provider_message' not in details:
            message = sanitize_provider_message(str(exc))
            if message:
                details['provider_message'] = message
        return details
    message = sanitize_provider_message(str(exc))
    details = {'operation': operation}
    if message:
        details['provider_message'] = message
    return details


def _sync_failure(event, exc, *, operation, local_saved):
    details = _failure_details(exc, operation=operation)
    logger.error(
        'zoom_sync: operation=%s event_id=%s event_slug=%s '
        'zoom_meeting_id=%s http_status=%s provider_code=%s '
        'provider_message=%s',
        operation,
        event.pk,
        event.slug,
        event.zoom_meeting_id,
        details.get('http_status'),
        details.get('provider_code'),
        details.get('provider_message'),
    )

    if local_saved and operation == 'update_meeting':
        message = (
            'Zoom meeting update failed. The local event was saved, but Zoom '
            'may be out of date. Retry with POST '
            f'/api/events/{event.slug}/sync-zoom.'
        )
    elif local_saved:
        message = (
            'Zoom meeting cancellation failed. The local event was saved, '
            'but Zoom may be out of date.'
        )
    else:
        message = 'Zoom meeting sync failed. The local event was not changed.'

    diagnostic_parts = []
    if details.get('http_status') is not None:
        diagnostic_parts.append(f"HTTP {details['http_status']}")
    if details.get('provider_code') is not None:
        diagnostic_parts.append(f"provider code {details['provider_code']}")
    if details.get('provider_message'):
        diagnostic_parts.append(details['provider_message'])
    if diagnostic_parts:
        message += ' ' + '; '.join(diagnostic_parts) + '.'

    return ZoomSyncFailure(message=message, **details)


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
    except Exception as exc:  # noqa: BLE001 - fail soft by contract
        return _sync_failure(
            event,
            exc,
            operation='update_meeting',
            local_saved=True,
        )
    return None


def force_sync_zoom_meeting(event):
    """PATCH Zoom from stored event state, independent of change detection.

    This deliberate retry never saves the event or changes its external
    meeting identity. Repeating it converges the same meeting toward the same
    local title/schedule/settings payload.
    """
    try:
        update_meeting(event)
    except Exception as exc:  # noqa: BLE001 - API must map provider/network errors
        return _sync_failure(
            event,
            exc,
            operation='update_meeting',
            local_saved=False,
        )
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

    try:
        delete_meeting(event)
    except Exception as exc:  # noqa: BLE001 - fail soft by contract
        return _sync_failure(
            event,
            exc,
            operation='delete_meeting',
            local_saved=True,
        )

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
