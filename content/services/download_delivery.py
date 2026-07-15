"""Private-S3 delivery and durable one-time grants for downloads."""

import datetime
import hashlib
import secrets

import boto3
from django.core import signing
from django.utils import timezone

from integrations.config import get_config

DEFAULT_DOWNLOADS_REGION = 'eu-central-1'
DEFAULT_PRESIGNED_TTL_SECONDS = 300
DEFAULT_DELIVERY_TOKEN_TTL_HOURS = 24
DOWNLOAD_GRANT_SALT = 'content.download-delivery.v1'
DOWNLOAD_SURFACES = {'catalog', 'detail', 'shortcode'}


def normalize_download_surface(value):
    surface = str(value or '').strip().lower()
    return surface if surface in DOWNLOAD_SURFACES else 'detail'


def _bounded_int_config(key, default, minimum, maximum):
    raw = get_config(key, str(default))
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    if value < minimum or value > maximum:
        return default
    return value


def get_downloads_s3_config():
    return {
        'bucket': get_config('AWS_S3_DOWNLOADS_BUCKET'),
        'region': get_config(
            'AWS_S3_DOWNLOADS_REGION', DEFAULT_DOWNLOADS_REGION,
        ) or DEFAULT_DOWNLOADS_REGION,
        'access_key_id': get_config('AWS_ACCESS_KEY_ID'),
        'secret_access_key': get_config('AWS_SECRET_ACCESS_KEY'),
    }


def get_presigned_ttl_seconds():
    return _bounded_int_config(
        'DOWNLOAD_PRESIGNED_URL_TTL_SECONDS',
        DEFAULT_PRESIGNED_TTL_SECONDS,
        60,
        900,
    )


def get_delivery_token_ttl_hours():
    return _bounded_int_config(
        'DOWNLOAD_DELIVERY_TOKEN_TTL_HOURS',
        DEFAULT_DELIVERY_TOKEN_TTL_HOURS,
        1,
        72,
    )


def create_delivery_grant(
    user,
    download,
    *,
    newsletter_opt_in=False,
    surface='detail',
):
    """Create a durable grant and return an opaque signed bearer token."""
    from content.models import DownloadDeliveryGrant

    secret = secrets.token_urlsafe(32)
    grant = DownloadDeliveryGrant.objects.create(
        user=user,
        download=download,
        token_hash=hashlib.sha256(secret.encode()).hexdigest(),
        newsletter_opt_in=bool(newsletter_opt_in),
        surface=normalize_download_surface(surface),
        expires_at=timezone.now() + datetime.timedelta(
            hours=get_delivery_token_ttl_hours(),
        ),
    )
    return signing.dumps(
        {'grant_id': str(grant.pk), 'secret': secret},
        salt=DOWNLOAD_GRANT_SALT,
        compress=True,
    )


def unpack_delivery_grant_token(token):
    """Validate signature/age and return `(grant_id, secret)`."""
    payload = signing.loads(
        token,
        salt=DOWNLOAD_GRANT_SALT,
        max_age=get_delivery_token_ttl_hours() * 3600,
    )
    grant_id = payload.get('grant_id')
    secret = payload.get('secret')
    if not grant_id or not secret:
        raise signing.BadSignature('Incomplete download grant')
    return grant_id, secret


def grant_secret_matches(grant, secret):
    expected = grant.token_hash
    supplied = hashlib.sha256(secret.encode()).hexdigest()
    return secrets.compare_digest(expected, supplied)


def build_download_presigned_url(download):
    """Mint a short-lived attachment URL after the caller authorizes access."""
    config = get_downloads_s3_config()
    if not config['bucket'] or not download.delivery_ready:
        raise ValueError('Download storage is not configured or ready')
    client = boto3.client(
        's3',
        region_name=config['region'],
        aws_access_key_id=config['access_key_id'],
        aws_secret_access_key=config['secret_access_key'],
    )
    return client.generate_presigned_url(
        'get_object',
        Params={
            'Bucket': config['bucket'],
            'Key': download.storage_key,
            'ResponseContentDisposition': (
                f'attachment; filename="{download.safe_filename}"'
            ),
            'ResponseContentType': download.resolved_mime_type,
        },
        ExpiresIn=get_presigned_ttl_seconds(),
    )


def verify_download_object_exists(storage_key):
    """Fail sync unless the configured private object exists and is readable."""
    config = get_downloads_s3_config()
    if not config['bucket']:
        raise ValueError('Private download bucket is not configured')
    client = boto3.client(
        's3',
        region_name=config['region'],
        aws_access_key_id=config['access_key_id'],
        aws_secret_access_key=config['secret_access_key'],
    )
    try:
        client.head_object(Bucket=config['bucket'], Key=storage_key)
    except Exception as exc:
        raise ValueError(
            'Private download object is missing or inaccessible',
        ) from exc


def apply_explicit_newsletter_opt_in(user):
    """Record a confirmed opt-in without changing unchecked requests."""
    preferences = dict(user.email_preferences or {})
    preferences['newsletter'] = True
    user.email_preferences = preferences
    user.unsubscribed = False
    user.save(update_fields=['email_preferences', 'unsubscribed'])
