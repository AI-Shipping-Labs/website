"""
Background task for downloading Zoom recordings and uploading to S3.

Flow:
1. Stream the Zoom MP4 to a temporary file (bounded RAM)
2. Upload that file to S3 at recordings/{year}/{event-slug}.mp4
3. Store S3 URL on Event record
"""

import logging
import os
import tempfile

import requests

from integrations.config import recording_auto_publish_on_s3_upload_enabled
from jobs.tasks.recordings_s3 import (
    build_recording_s3_key,
    get_recordings_s3_config,
    upload_recording_mp4,
)

logger = logging.getLogger(__name__)

RECORDING_UPLOAD_TASK_TIMEOUT_SECONDS = 900
RECORDING_UPLOAD_HTTP_TIMEOUT_SECONDS = 600
RECORDING_UPLOAD_TASK_RETRY_SECONDS = 960
RECORDING_UPLOAD_MAX_RETRIES = 3
RECORDING_DOWNLOAD_CHUNK_SIZE = 8192


def retry_stuck_recording_uploads(limit=None):
    """Scheduled reclaim for expired Zoom-to-S3 upload leases."""
    from events.services.recording_upload import (
        RECORDING_UPLOAD_RECLAIM_LIMIT,
    )
    from events.services.recording_upload import (
        retry_stuck_recording_uploads as reclaim,
    )

    if limit is None:
        limit = RECORDING_UPLOAD_RECLAIM_LIMIT
    return reclaim(limit=limit)


def upload_recording_to_s3(event_id, download_url):
    """Download a recording from Zoom and upload it to S3.

    Args:
        event_id: ID of the Event model instance.
        download_url: Zoom download URL for the recording file.

    Returns:
        dict with status and s3_url on success.

    Raises:
        Exception: If download or upload fails (will trigger retry via django-q2).
    """
    from events.models import Event

    try:
        event = Event.objects.get(id=event_id)
    except Event.DoesNotExist:
        logger.error('Event %s not found, skipping upload', event_id)
        return {'status': 'error', 'message': f'Event {event_id} not found'}

    from django.utils import timezone

    event.recording_upload_enqueued_at = timezone.now()
    event.save(update_fields=['recording_upload_enqueued_at', 'updated_at'])

    download_url = (download_url or event.recording_zoom_download_url or '').strip()
    if download_url and download_url != event.recording_zoom_download_url:
        event.recording_zoom_download_url = download_url
        event.save(update_fields=['recording_zoom_download_url', 'updated_at'])
    if not download_url:
        logger.error('No Zoom download URL for event %s, skipping upload', event_id)
        return {'status': 'error', 'message': 'No Zoom download URL'}

    s3_config = get_recordings_s3_config()

    if not s3_config.bucket:
        logger.error(
            'AWS_S3_RECORDINGS_BUCKET not configured, skipping upload for event %s',
            event_id,
        )
        return {'status': 'error', 'message': 'S3 bucket not configured'}

    s3_key = build_recording_s3_key(event)

    logger.info(
        'Starting download of recording for event "%s" from Zoom: %s',
        event.title, download_url,
    )

    zoom_download_url = _build_authenticated_download_url(download_url)
    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(prefix='zoom-recording-', suffix='.mp4')
        os.close(fd)
        file_size = _download_from_zoom(zoom_download_url, tmp_path)

        logger.info(
            'Downloaded %d bytes for event "%s", uploading to S3 bucket %s at %s',
            file_size, event.title, s3_config.bucket, s3_key,
        )

        s3_url = upload_recording_mp4(tmp_path, s3_config, s3_key)
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except FileNotFoundError:
                pass

    # Store S3 URL on event. Issue #1134 (Phase B): when auto-publish is
    # enabled (default on), flip the event live in the same save so entitled
    # members can watch immediately and the host "available to watch"
    # notification is truthful. The model's save() syncs published_at.
    event.recording_s3_url = s3_url
    update_fields = ['recording_s3_url', 'updated_at']
    if recording_auto_publish_on_s3_upload_enabled() and not event.published:
        event.published = True
        update_fields += ['published', 'published_at']
        logger.info(
            'Auto-publishing event "%s" after successful S3 recording upload',
            event.title,
        )
    event.save(update_fields=update_fields)

    logger.info(
        'Successfully uploaded recording for event "%s" to S3: %s',
        event.title, s3_url,
    )

    from events.services.recording_ready_notification import notify_recording_ready

    try:
        host_notification = notify_recording_ready(event)
    except Exception as exc:
        logger.exception(
            'Recording-ready host notification failed for event "%s" after '
            'successful S3 upload',
            event.title,
        )
        host_notification = {
            'status': 'error',
            'recipient_count': 0,
            'attempted_recipient_count': 0,
            'skipped_reason': 'notification_error',
            'email_log_ids': [],
            'results': [{
                'status': 'error',
                'reason': exc.__class__.__name__,
            }],
        }

    return {
        'status': 'ok',
        's3_url': s3_url,
        'event_id': event_id,
        'host_notification_status': host_notification['status'],
        'host_notification_recipient_count': host_notification['recipient_count'],
        'host_notification_attempted_recipient_count': (
            host_notification['attempted_recipient_count']
        ),
        'host_notification_skipped_reason': host_notification['skipped_reason'],
        'host_notification_email_log_ids': host_notification['email_log_ids'],
        'host_notification_results': host_notification['results'],
    }


def _build_authenticated_download_url(download_url):
    """Add Zoom access token to the download URL.

    Zoom download URLs require authentication via access_token query parameter.

    Args:
        download_url: The Zoom download URL.

    Returns:
        str: Download URL with access token appended.
    """
    from integrations.services.zoom import get_access_token

    token = get_access_token()
    separator = '&' if '?' in download_url else '?'
    return f'{download_url}{separator}access_token={token}'


def _download_from_zoom(url, dest_path):
    """Stream a Zoom recording to ``dest_path``.

    Peak RAM stays at the chunk size. The HTTP response is closed in
    ``finally`` even when ``raise_for_status`` or a later chunk fails.

    Args:
        url: The authenticated download URL.
        dest_path: Filesystem path to write the MP4.

    Returns:
        int: Number of bytes written.

    Raises:
        requests.HTTPError: If the download fails.
    """
    response = None
    try:
        response = requests.get(
            url,
            stream=True,
            timeout=RECORDING_UPLOAD_HTTP_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        written = 0
        with open(dest_path, 'wb') as dest:
            for chunk in response.iter_content(chunk_size=RECORDING_DOWNLOAD_CHUNK_SIZE):
                if not chunk:
                    continue
                dest.write(chunk)
                written += len(chunk)
        return written
    finally:
        if response is not None:
            response.close()
