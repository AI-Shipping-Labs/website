"""Shared S3 helpers for recording upload tasks."""

import io
import os
import tempfile
from dataclasses import dataclass
from urllib.parse import urlparse

import boto3

from integrations.config import get_config

DEFAULT_RECORDINGS_REGION = 'eu-central-1'
RECORDING_CONTENT_TYPE = 'video/mp4'


@dataclass(frozen=True)
class RecordingsS3Config:
    bucket: str
    region: str
    access_key_id: str | None
    secret_access_key: str | None


def get_recordings_s3_config():
    """Load recordings S3 settings through runtime integration config."""
    return RecordingsS3Config(
        bucket=get_config('AWS_S3_RECORDINGS_BUCKET'),
        region=get_config(
            'AWS_S3_RECORDINGS_REGION',
            DEFAULT_RECORDINGS_REGION,
        ) or DEFAULT_RECORDINGS_REGION,
        access_key_id=get_config('AWS_ACCESS_KEY_ID'),
        secret_access_key=get_config('AWS_SECRET_ACCESS_KEY'),
    )


def get_recordings_s3_client(config):
    return boto3.client(
        's3',
        region_name=config.region,
        aws_access_key_id=config.access_key_id,
        aws_secret_access_key=config.secret_access_key,
    )


def build_recording_s3_key(event):
    return f'recordings/{event.start_datetime.year}/{event.slug}.mp4'


def build_recording_s3_url(bucket, region, key):
    return f'https://{bucket}.s3.{region}.amazonaws.com/{key}'


def upload_recording_mp4(file_data, config, key):
    s3_client = get_recordings_s3_client(config)
    s3_client.upload_fileobj(
        io.BytesIO(file_data),
        config.bucket,
        key,
        ExtraArgs={
            'ContentType': RECORDING_CONTENT_TYPE,
        },
    )
    return build_recording_s3_url(config.bucket, config.region, key)


def download_recording_to_temp_file(s3_url, config):
    s3_key = extract_s3_key(s3_url, config.bucket, config.region)
    s3_client = get_recordings_s3_client(config)

    temp_fd, temp_path = tempfile.mkstemp(suffix='.mp4')
    os.close(temp_fd)

    s3_client.download_file(config.bucket, s3_key, temp_path)
    return temp_path, s3_key


def extract_s3_key(s3_url, bucket, region):
    prefix = build_recording_s3_url(bucket, region, '')
    if s3_url.startswith(prefix):
        return s3_url[len(prefix):]

    parsed = urlparse(s3_url)
    return parsed.path.lstrip('/')
