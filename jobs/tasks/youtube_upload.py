"""
Background task for downloading a recording from S3 and uploading to YouTube.

Flow:
1. Download recording file from S3
2. Upload to YouTube via the YouTube Data API v3 (resumable upload)
3. Store YouTube URL on the Recording record
"""

import logging
import os
import tempfile

import boto3
from botocore.exceptions import ClientError
from django.conf import settings

logger = logging.getLogger(__name__)


def upload_recording_to_youtube(recording_id):
    """Download a recording from S3 and upload it to YouTube.

    Args:
        recording_id: ID of the Recording model instance.

    Returns:
        dict with status and youtube_url on success.

    Raises:
        Exception: If download or upload fails (will trigger retry via django-q2).
    """
    from content.models import Recording

    try:
        recording = Recording.objects.get(id=recording_id)
    except Recording.DoesNotExist:
        logger.error('Recording %s not found, skipping YouTube upload', recording_id)
        return {'status': 'error', 'message': f'Recording {recording_id} not found'}

    if not recording.s3_url:
        logger.error(
            'Recording %s has no S3 URL, skipping YouTube upload',
            recording_id,
        )
        return {'status': 'error', 'message': 'Recording has no S3 URL'}

    if recording.youtube_url:
        logger.warning(
            'Recording %s already has a YouTube URL: %s',
            recording_id, recording.youtube_url,
        )
        return {'status': 'skipped', 'message': 'Recording already has YouTube URL'}

    # Build video metadata from recording and linked event
    title = recording.title
    description = _build_description(recording)
    tags = recording.tags if recording.tags else []

    logger.info(
        'Starting YouTube upload for recording "%s" (id=%s)',
        recording.title, recording_id,
    )

    # Download from S3 to a temp file
    temp_path = _download_from_s3(recording)

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

        # Store YouTube URL on recording
        recording.youtube_url = result['youtube_url']
        recording.save(update_fields=['youtube_url', 'updated_at'])

        logger.info(
            'Successfully uploaded recording "%s" to YouTube: %s',
            recording.title, result['youtube_url'],
        )

        return {
            'status': 'ok',
            'youtube_url': result['youtube_url'],
            'video_id': result['video_id'],
            'recording_id': recording_id,
        }
    finally:
        # Clean up temp file
        if os.path.exists(temp_path):
            os.unlink(temp_path)
            logger.debug('Cleaned up temp file: %s', temp_path)


def _build_description(recording):
    """Build a YouTube video description from the recording and event metadata.

    Args:
        recording: Recording model instance.

    Returns:
        str: Video description text.
    """
    parts = []

    if recording.description:
        parts.append(recording.description)

    if recording.event:
        event = recording.event
        if event.description and event.description != recording.description:
            parts.append(event.description)

    parts.append(f'Date: {recording.formatted_date()}')

    if recording.learning_objectives:
        parts.append('')
        parts.append('What you will learn:')
        for obj in recording.learning_objectives:
            parts.append(f'- {obj}')

    parts.append('')
    parts.append('AI Shipping Labs - https://aishippinglabs.com')

    return '\n'.join(parts)


def _download_from_s3(recording):
    """Download the recording file from S3 to a temporary file.

    Args:
        recording: Recording model instance with s3_url set.

    Returns:
        str: Path to the temporary file.

    Raises:
        ClientError: If the S3 download fails.
    """
    bucket = settings.AWS_S3_RECORDINGS_BUCKET
    region = settings.AWS_S3_RECORDINGS_REGION

    if not bucket:
        raise ValueError('AWS_S3_RECORDINGS_BUCKET not configured')

    # Extract S3 key from the URL
    # URL format: https://{bucket}.s3.{region}.amazonaws.com/{key}
    s3_key = _extract_s3_key(recording.s3_url, bucket, region)

    s3_client = boto3.client(
        's3',
        region_name=region,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
    )

    # Download to temp file
    temp_fd, temp_path = tempfile.mkstemp(suffix='.mp4')
    os.close(temp_fd)

    logger.info(
        'Downloading recording from S3: s3://%s/%s -> %s',
        bucket, s3_key, temp_path,
    )

    s3_client.download_file(bucket, s3_key, temp_path)

    file_size = os.path.getsize(temp_path)
    logger.info(
        'Downloaded %d bytes from S3 for recording "%s"',
        file_size, recording.title,
    )

    return temp_path


def _extract_s3_key(s3_url, bucket, region):
    """Extract the S3 object key from an S3 URL.

    Args:
        s3_url: Full S3 URL.
        bucket: S3 bucket name.
        region: AWS region.

    Returns:
        str: The S3 object key.
    """
    prefix = f'https://{bucket}.s3.{region}.amazonaws.com/'
    if s3_url.startswith(prefix):
        return s3_url[len(prefix):]

    # Fallback: try to extract key after the bucket hostname
    from urllib.parse import urlparse
    parsed = urlparse(s3_url)
    return parsed.path.lstrip('/')
