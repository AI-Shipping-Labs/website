"""
Background task for downloading Zoom recordings and uploading to S3.

Flow:
1. Download recording file from Zoom (authenticated with access token)
2. Upload to S3 at recordings/{year}/{event-slug}.mp4
3. Store S3 URL on Event record
"""

import io
import logging

import boto3
import requests
from botocore.exceptions import ClientError
from django.conf import settings

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

    bucket = settings.AWS_S3_RECORDINGS_BUCKET
    region = settings.AWS_S3_RECORDINGS_REGION

    if not bucket:
        logger.error(
            'AWS_S3_RECORDINGS_BUCKET not configured, skipping upload for event %s',
            event_id,
        )
        return {'status': 'error', 'message': 'S3 bucket not configured'}

    # Build S3 key: recordings/{year}/{event-slug}.mp4
    year = event.start_datetime.year
    s3_key = f'recordings/{year}/{event.slug}.mp4'

    logger.info(
        'Starting download of recording for event "%s" from Zoom: %s',
        event.title, download_url,
    )

    # Download from Zoom with access token
    zoom_download_url = _build_authenticated_download_url(download_url)
    file_data = _download_from_zoom(zoom_download_url)

    logger.info(
        'Downloaded %d bytes for event "%s", uploading to S3 bucket %s at %s',
        len(file_data), event.title, bucket, s3_key,
    )

    # Upload to S3
    s3_url = _upload_to_s3(file_data, bucket, s3_key, region)

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


def _upload_to_s3(file_data, bucket, key, region):
    """Upload file data to S3.

    Args:
        file_data: bytes of the file to upload.
        bucket: S3 bucket name.
        key: S3 object key.
        region: AWS region for the S3 bucket.

    Returns:
        str: The S3 URL of the uploaded file.

    Raises:
        ClientError: If the S3 upload fails.
    """
    s3_client = boto3.client(
        's3',
        region_name=region,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
    )

    s3_client.upload_fileobj(
        io.BytesIO(file_data),
        bucket,
        key,
        ExtraArgs={
            'ContentType': 'video/mp4',
        },
    )

    s3_url = f'https://{bucket}.s3.{region}.amazonaws.com/{key}'
    return s3_url
