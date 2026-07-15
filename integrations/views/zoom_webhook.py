"""Zoom webhook endpoint for handling recording.completed events.

Endpoint: POST /api/webhooks/zoom

When Zoom sends a recording.completed webhook:
1. Validates the webhook signature
2. Matches the meeting_id to an Event record
3. Sets recording fields directly on the Event

Issue #713: the webhook no longer writes ``event.status='completed'``.
The event becomes "past" automatically once ``end_datetime`` passes
via the time-derived ``Event.is_past`` property; the daily
``complete_finished_events`` cron refreshes the stored status for
staff bookkeeping.
"""

import json
import logging

from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from events.models import Event
from integrations.models import WebhookLog
from integrations.services.zoom import (
    build_url_validation_encrypted_token,
    validate_webhook_signature,
)

logger = logging.getLogger(__name__)

PREFERRED_VIDEO_RECORDING_TYPES = (
    'shared_screen_with_speaker_view',
    'shared_screen',
    'active_speaker',
)


@csrf_exempt
@require_POST
def zoom_webhook(request):
    """Handle incoming Zoom webhooks.

    Validates the signature, logs the webhook, and processes
    recording.completed events.

    Returns:
        200 on success
        400 on invalid signature or malformed payload
    """
    # Validate webhook signature
    if not validate_webhook_signature(request):
        logger.warning('Invalid Zoom webhook signature')
        return JsonResponse(
            {'error': 'Invalid webhook signature'},
            status=400,
        )

    # Parse the payload
    try:
        payload = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse(
            {'error': 'Invalid JSON payload'},
            status=400,
        )

    event_type = payload.get('event', '')

    # Handle Zoom's URL validation challenge (endpoint validation)
    if event_type == 'endpoint.url_validation':
        plain_token = payload.get('payload', {}).get('plainToken', '')
        if plain_token:
            encrypted_token = build_url_validation_encrypted_token(plain_token)
            return JsonResponse({
                'plainToken': plain_token,
                'encryptedToken': encrypted_token,
            })

    # Log the webhook
    webhook_log = WebhookLog.objects.create(
        service='zoom',
        event_type=event_type,
        payload=payload,
        processed=False,
    )

    # Process recording.completed
    if event_type == 'recording.completed':
        try:
            _handle_recording_completed(payload, webhook_log)
        except Exception as e:
            logger.exception('Error processing recording.completed webhook')
            return JsonResponse(
                {'status': 'error', 'message': str(e)},
                status=200,  # Return 200 to avoid Zoom retries
            )

    return JsonResponse({'status': 'ok'})


def _handle_recording_completed(payload, webhook_log):
    """Process a recording.completed webhook payload.

    Sets recording fields directly on the matched Event and enqueues a
    background job to download the recording from Zoom and upload it
    to S3. Issue #713: the ``status='completed'`` write was dropped
    here; ``Event.is_past`` is now time-derived.

    Args:
        payload: The parsed JSON payload from Zoom.
        webhook_log: The WebhookLog instance for this webhook.
    """
    zoom_payload = payload.get('payload', {})
    object_data = zoom_payload.get('object', {})
    meeting_id = str(object_data.get('id', ''))

    if not meeting_id:
        logger.warning('recording.completed webhook missing meeting ID')
        return

    # Extract recording URLs from Zoom payload
    recording_files = object_data.get('recording_files', [])
    video_url, download_url = _select_preferred_video(recording_files)
    transcript_url = ''
    # Extract transcript URL (audio_transcript VTT file)
    for rec_file in recording_files:
        if rec_file.get('recording_type') == 'audio_transcript':
            transcript_url = rec_file.get('download_url', '')
            break

    # Fallback: use the share_url from the object if available
    if not video_url:
        video_url = object_data.get('share_url', '')

    # The Event state, queue row, durable enqueue marker, and processed flag
    # commit together. A queue failure rolls all four back, so a later signed
    # replay can recover instead of seeing a stored play URL and skipping the
    # only usable download forever. ``select_for_update`` serializes concurrent
    # duplicate deliveries for the same meeting.
    with transaction.atomic():
        try:
            event = Event.objects.select_for_update().get(
                zoom_meeting_id=meeting_id,
            )
        except Event.DoesNotExist:
            logger.warning(
                'No event found for Zoom meeting ID %s', meeting_id,
            )
            return

        update_fields = []
        if video_url and not event.recording_url:
            event.recording_url = video_url
            update_fields.append('recording_url')
        if transcript_url and not event.transcript_url:
            event.transcript_url = transcript_url
            update_fields.append('transcript_url')

        should_enqueue_upload = (
            bool(download_url)
            and not event.recording_s3_url
            and event.recording_upload_enqueued_at is None
        )

        if should_enqueue_upload:
            from jobs.tasks import async_task, build_task_name
            async_task(
                'jobs.tasks.recording_upload.upload_recording_to_s3',
                event.id,
                download_url,
                max_retries=3,
                task_name=build_task_name(
                    'Upload Zoom recording',
                    f'event #{event.id} {event.title}',
                    'Zoom webhook',
                ),
            )
            event.recording_upload_enqueued_at = timezone.now()
            update_fields.append('recording_upload_enqueued_at')

        if update_fields:
            update_fields.append('updated_at')
            event.save(update_fields=update_fields)

        webhook_log.processed = True
        webhook_log.save(update_fields=['processed'])

    logger.info(
        'Set recording fields on event "%s" (slug=%s) from Zoom meeting %s',
        event.title, event.slug, meeting_id,
    )

    if should_enqueue_upload:
        logger.info(
            'Enqueued S3 upload job for event "%s" (id=%s)',
            event.title, event.id,
        )
    elif download_url:
        logger.info(
            'Skipping duplicate S3 upload job for event "%s" (id=%s)',
            event.title, event.id,
        )
    else:
        logger.warning(
            'No download URL available for event "%s", skipping S3 upload',
            event.title,
        )


def _select_preferred_video(recording_files):
    """Return matching ``(play_or_download_url, download_url)``.

    Prefer the canonical recording types in product order, but only select a
    row for upload when it has a usable download URL. A play-only preferred row
    must not hide a later downloadable row. When no download exists, retain the
    best play URL for operator/source visibility without enqueuing an upload.
    """
    for recording_type in PREFERRED_VIDEO_RECORDING_TYPES:
        for rec_file in recording_files:
            if rec_file.get('recording_type') != recording_type:
                continue
            download_url = (rec_file.get('download_url') or '').strip()
            if download_url:
                video_url = (rec_file.get('play_url') or '').strip()
                return video_url or download_url, download_url

    for recording_type in PREFERRED_VIDEO_RECORDING_TYPES:
        for rec_file in recording_files:
            if rec_file.get('recording_type') != recording_type:
                continue
            video_url = (
                rec_file.get('play_url') or rec_file.get('download_url') or ''
            ).strip()
            if video_url:
                return video_url, ''

    return '', ''
