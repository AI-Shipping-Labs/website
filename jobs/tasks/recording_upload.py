"""
Background task for downloading Zoom recordings and uploading to S3.

Flow:
1. Download recording file from Zoom (authenticated with access token)
2. Upload to S3 at recordings/{year}/{event-slug}.mp4
3. Store S3 URL on Event record
"""

import logging

import requests

from jobs.tasks.recordings_s3 import (
    build_recording_s3_key,
    get_recordings_s3_config,
    upload_recording_mp4,
)

logger = logging.getLogger(__name__)


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

    # Download from Zoom with access token
    zoom_download_url = _build_authenticated_download_url(download_url)
    file_data = _download_from_zoom(zoom_download_url)

    logger.info(
        'Downloaded %d bytes for event "%s", uploading to S3 bucket %s at %s',
        len(file_data), event.title, s3_config.bucket, s3_key,
    )

    s3_url = upload_recording_mp4(file_data, s3_config, s3_key)

    # Store S3 URL on event
    event.recording_s3_url = s3_url
    event.save(update_fields=['recording_s3_url', 'updated_at'])

    logger.info(
        'Successfully uploaded recording for event "%s" to S3: %s',
        event.title, s3_url,
    )

    return {'status': 'ok', 's3_url': s3_url, 'event_id': event_id}


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


def _download_from_zoom(url):
    """Download a file from Zoom.

    Args:
        url: The authenticated download URL.

    Returns:
        bytes: The downloaded file content.

    Raises:
        requests.HTTPError: If the download fails.
    """
    response = requests.get(url, stream=True, timeout=600)
    response.raise_for_status()

    chunks = []
    for chunk in response.iter_content(chunk_size=8192):
        chunks.append(chunk)

    return b''.join(chunks)
