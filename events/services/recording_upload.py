"""Lease and reclaim helpers for Zoom-to-S3 recording uploads."""

from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

RECORDING_UPLOAD_LEASE_TTL = timedelta(minutes=20)
RECORDING_UPLOAD_RECLAIM_LIMIT = 20

RECORDING_UPLOAD_STATUS_UPLOADED = 'uploaded'
RECORDING_UPLOAD_STATUS_IN_PROGRESS = 'in_progress'
RECORDING_UPLOAD_STATUS_STUCK = 'stuck'
RECORDING_UPLOAD_STATUS_IDLE = 'idle'


def recording_upload_lease_is_active(event, now=None):
    """Return True when the event has an unexpired upload lease."""
    claimed_at = event.recording_upload_enqueued_at
    if claimed_at is None:
        return False
    if now is None:
        now = timezone.now()
    return claimed_at + RECORDING_UPLOAD_LEASE_TTL > now


def recording_upload_status(event, now=None):
    """Return the derived operator status for a recording upload."""
    if event.recording_s3_url:
        return RECORDING_UPLOAD_STATUS_UPLOADED
    if recording_upload_lease_is_active(event, now=now):
        return RECORDING_UPLOAD_STATUS_IN_PROGRESS
    if event.recording_zoom_download_url:
        return RECORDING_UPLOAD_STATUS_STUCK
    return RECORDING_UPLOAD_STATUS_IDLE


def enqueue_recording_upload_task(event, download_url, *, source):
    """Enqueue ``upload_recording_to_s3`` with the recording-specific timeouts."""
    from jobs.tasks import async_task, build_task_name
    from jobs.tasks.recording_upload import (
        RECORDING_UPLOAD_MAX_RETRIES,
        RECORDING_UPLOAD_TASK_RETRY_SECONDS,
        RECORDING_UPLOAD_TASK_TIMEOUT_SECONDS,
    )

    return async_task(
        'jobs.tasks.recording_upload.upload_recording_to_s3',
        event.id,
        download_url,
        max_retries=RECORDING_UPLOAD_MAX_RETRIES,
        retry_backoff=RECORDING_UPLOAD_TASK_RETRY_SECONDS,
        timeout=RECORDING_UPLOAD_TASK_TIMEOUT_SECONDS,
        task_name=build_task_name(
            'Upload Zoom recording',
            f'event #{event.id} {event.title}',
            source,
        ),
    )


def claim_and_enqueue_recording_upload(event_id, *, source):
    """Atomically claim an expired/idle lease and enqueue the upload task.

    Uses ``select_for_update`` so two workers cannot enqueue the same event.
    Returns ``(event_or_none, result)`` where result is ``queued`` or a
    derived status (``uploaded`` / ``in_progress`` / ``idle``).
    """
    from events.models import Event

    with transaction.atomic():
        event = (
            Event.objects.select_for_update()
            .filter(pk=event_id)
            .first()
        )
        if event is None:
            return None, 'missing'
        if event.recording_s3_url:
            return event, RECORDING_UPLOAD_STATUS_UPLOADED
        if recording_upload_lease_is_active(event):
            return event, RECORDING_UPLOAD_STATUS_IN_PROGRESS
        download_url = (event.recording_zoom_download_url or '').strip()
        if not download_url:
            return event, RECORDING_UPLOAD_STATUS_IDLE
        enqueue_recording_upload_task(event, download_url, source=source)
        event.recording_upload_enqueued_at = timezone.now()
        event.save(update_fields=['recording_upload_enqueued_at', 'updated_at'])
        return event, 'queued'


def retry_stuck_recording_uploads(limit=RECORDING_UPLOAD_RECLAIM_LIMIT):
    """Reclaim expired recording-upload leases, capped per pass."""
    from events.models import Event

    now = timezone.now()
    lease_cutoff = now - RECORDING_UPLOAD_LEASE_TTL
    candidate_ids = list(
        Event.objects.filter(recording_s3_url='')
        .exclude(recording_zoom_download_url='')
        .filter(
            Q(recording_upload_enqueued_at__isnull=True)
            | Q(recording_upload_enqueued_at__lte=lease_cutoff),
        )
        .order_by('id')
        .values_list('id', flat=True)[:limit]
    )
    queued = 0
    for event_id in candidate_ids:
        _event, result = claim_and_enqueue_recording_upload(
            event_id,
            source='Stuck recording reclaim',
        )
        if result == 'queued':
            queued += 1
    return {
        'queued': queued,
        'examined': len(candidate_ids),
        'limit': limit,
    }
