"""
Background task for downloading a recording from S3 and uploading to YouTube.

Flow:
1. Download recording file from S3
2. Upload to YouTube via the YouTube Data API v3 (resumable upload)
3. Store YouTube URL on the Event record
"""

import logging
import os

from django.conf import settings

from jobs.tasks.recordings_s3 import (
    download_recording_to_temp_file,
    get_recordings_s3_config,
)

logger = logging.getLogger(__name__)


def upload_recording_to_youtube(event_id):
    """Download a recording from S3 and upload it to YouTube.

    Args:
        event_id: ID of the Event model instance.

    Returns:
        dict with status and youtube_url on success.

    Raises:
        Exception: If download or upload fails (will trigger retry via django-q2).
    """
    from events.models import Event

    try:
        event = Event.objects.get(id=event_id)
    except Event.DoesNotExist:
        logger.error('Event %s not found, skipping YouTube upload', event_id)
        return {'status': 'error', 'message': f'Event {event_id} not found'}

    if not event.recording_s3_url:
        logger.error(
            'Event %s has no S3 URL, skipping YouTube upload',
            event_id,
        )
        return {'status': 'error', 'message': 'Event has no S3 URL'}

    if event.recording_url:
        logger.warning(
            'Event %s already has a recording URL: %s',
            event_id, event.recording_url,
        )
        return {'status': 'skipped', 'message': 'Event already has recording URL'}

    # Build video metadata from event
    title = event.title
    description = _build_description(event)
    tags = event.tags if event.tags else []

    logger.info(
        'Starting YouTube upload for event "%s" (id=%s)',
        event.title, event_id,
    )

    # Download from S3 to a temp file
    temp_path = _download_from_s3(event)

    try:
        # Upload to YouTube
        from integrations.services.youtube import upload_video

        result = upload_video(
            file_path=temp_path,
            title=title,
            description=description,
            tags=tags,
            privacy='unlisted',
        )

        # Store YouTube URL on event
        event.recording_url = result['youtube_url']
        event.save(update_fields=['recording_url', 'updated_at'])

        logger.info(
            'Successfully uploaded event "%s" to YouTube: %s',
            event.title, result['youtube_url'],
        )

        return {
            'status': 'ok',
            'youtube_url': result['youtube_url'],
            'video_id': result['video_id'],
            'event_id': event_id,
        }
    finally:
        # Clean up temp file
        if os.path.exists(temp_path):
            os.unlink(temp_path)
            logger.debug('Cleaned up temp file: %s', temp_path)


def _build_description(event):
    """Build a YouTube video description from the event metadata.

    Args:
        event: Event model instance.

    Returns:
        str: Video description text.
    """
    parts = []

    if event.description:
        parts.append(event.description)

    parts.append(f'Date: {event.formatted_date()}')

    if event.learning_objectives:
        parts.append('')
        parts.append('What you will learn:')
        for obj in event.learning_objectives:
            parts.append(f'- {obj}')

    parts.append('')
    parts.append(f'AI Shipping Labs - {settings.SITE_BASE_URL}')

    return '\n'.join(parts)


def _download_from_s3(event):
    """Download the recording file from S3 to a temporary file.

    Args:
        event: Event model instance with recording_s3_url set.

    Returns:
        str: Path to the temporary file.

    Raises:
        ClientError: If the S3 download fails.
    """
    s3_config = get_recordings_s3_config()

    if not s3_config.bucket:
        raise ValueError('AWS_S3_RECORDINGS_BUCKET not configured')

    temp_path, s3_key = download_recording_to_temp_file(event.recording_s3_url, s3_config)

    file_size = os.path.getsize(temp_path)
    logger.info(
        'Downloaded %d bytes from S3 for event "%s"',
        file_size, event.title,
    )

    return temp_path
