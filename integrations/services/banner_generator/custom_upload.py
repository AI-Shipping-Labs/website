"""Custom banner upload + safe-delete service (issue #931).

Operators can upload a custom banner / social image from a Studio content
edit page. The image lands on the content CDN bucket under
``custom-banners/<content_type>/<id>-<uuid>.<ext>`` and its CDN URL is
persisted to the record's ``custom_banner_url`` field — a sync-safe
override that beats the generated banner but loses to a frontmatter
``cover_image_url`` (see ``resolve.effective_banner_url``).

This module is pure service code (no HTTP / request objects) so it is
unit-testable in isolation. The Studio view layer
(:mod:`studio.views.banner_upload`) handles auth, ``require_POST``, message
flashing, and redirects, then calls :func:`upload_custom_banner` /
:func:`remove_custom_banner` here.

The S3 client construction and the narrow safe-delete mirror the existing
banner-generator pattern in
:mod:`integrations.services.banner_generator.tasks` — config keys are read
via :func:`integrations.config.get_config` so Studio overrides apply with no
redeploy, and cleanup only ever deletes keys under the
``custom-banners/<type>/`` prefix on the configured CDN base.
"""

import logging
import uuid
from urllib.parse import unquote, urlparse

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from integrations.config import get_config
from integrations.services.banner_generator.content_models import (
    SUPPORTED_CONTENT_TYPES,
)
from integrations.services.banner_generator.tasks import cdn_url_for_key

logger = logging.getLogger(__name__)

# Allowed upload MIME types mapped to their canonical file extension. The
# extension is what we store in the S3 key; the MIME type is what we send as
# the object's ``ContentType`` so the CDN serves it correctly. Registered as
# config keys so an operator can widen/narrow the allow-list without a
# redeploy (issue #931); see ``settings_registry`` ``banner_generator`` group.
DEFAULT_ALLOWED_TYPES = 'image/jpeg,image/png,image/webp'
_EXT_BY_MIME = {
    'image/jpeg': 'jpg',
    'image/png': 'png',
    'image/webp': 'webp',
}
DEFAULT_MAX_UPLOAD_MB = 5
# CDN key prefix for operator uploads. Configurable so the operator can move
# custom banners under a different prefix without a code change; the
# safe-delete logic reads the same key so cleanup stays correct.
DEFAULT_KEY_PREFIX = 'custom-banners'


class CustomBannerUploadError(Exception):
    """Raised when a custom-banner upload cannot proceed.

    Carries an operator-facing ``message`` suitable for a Studio
    ``messages.error`` flash. Raised for validation failures (bad type,
    too large, empty file), missing CDN/bucket config, and S3 errors.
    """

    def __init__(self, message):
        self.message = str(message)
        super().__init__(self.message)


def allowed_content_types():
    """Return the set of accepted upload MIME types (lower-cased).

    Reads ``BANNER_UPLOAD_ALLOWED_TYPES`` via ``get_config`` (comma-separated)
    so the allow-list is Studio-editable. Falls back to the JPEG/PNG/WebP
    default. Unknown MIME types in the override that have no known extension
    are dropped so we never build an un-resolvable S3 key.
    """
    raw = get_config('BANNER_UPLOAD_ALLOWED_TYPES', DEFAULT_ALLOWED_TYPES)
    types = [t.strip().lower() for t in str(raw).split(',') if t.strip()]
    return {t for t in types if t in _EXT_BY_MIME}


def max_upload_bytes():
    """Return the max allowed upload size in bytes.

    Reads ``BANNER_UPLOAD_MAX_MB`` via ``get_config`` (an integer number of
    megabytes), defaulting to 5 MB. A non-positive or unparseable override
    falls back to the default so a bad setting never disables uploads
    entirely.
    """
    raw = get_config('BANNER_UPLOAD_MAX_MB', DEFAULT_MAX_UPLOAD_MB)
    try:
        mb = int(raw)
    except (TypeError, ValueError):
        mb = DEFAULT_MAX_UPLOAD_MB
    if mb <= 0:
        mb = DEFAULT_MAX_UPLOAD_MB
    return mb * 1024 * 1024


def key_prefix():
    """Return the configured CDN key prefix for custom banners (no slashes)."""
    raw = get_config('BANNER_UPLOAD_KEY_PREFIX', DEFAULT_KEY_PREFIX)
    return (str(raw) or DEFAULT_KEY_PREFIX).strip('/')


def is_upload_enabled():
    """Return True when the CDN base and content bucket are both configured.

    Mirrors the Regenerate button's enabled gate: without a CDN base we
    can't build a public URL, and without a bucket we can't upload. When
    either is missing the Studio control renders disabled and the POST view
    flashes a "not configured" warning.
    """
    cdn_base = (get_config('CONTENT_CDN_BASE', '') or '').strip()
    bucket = (get_config('AWS_S3_CONTENT_BUCKET', '') or '').strip()
    return bool(cdn_base) and bool(bucket)


def _s3_client():
    """Construct a boto3 S3 client from the content-bucket config keys.

    Reuses the same credential resolution as the sync pipeline and the
    banner render task: region + optional explicit access/secret keys read
    via ``get_config`` so a Studio override or the ECS task role both work.
    """
    region = get_config('AWS_S3_CONTENT_REGION', 'eu-west-1')
    client_kwargs = {'region_name': region}
    access_key = get_config('AWS_ACCESS_KEY_ID')
    secret_key = get_config('AWS_SECRET_ACCESS_KEY')
    if access_key and secret_key:
        client_kwargs.update(
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
        )
    return boto3.client('s3', **client_kwargs)


def _validate(upload):
    """Validate the uploaded file; return its (mime, ext) or raise.

    Checks the size against ``max_upload_bytes`` and the content type
    against ``allowed_content_types``. Raises :class:`CustomBannerUploadError`
    with a specific operator-facing message on the first failure. Does not
    touch S3 or the DB — pure validation.
    """
    if upload is None:
        raise CustomBannerUploadError('No image file was provided.')

    size = getattr(upload, 'size', None)
    if not size:
        raise CustomBannerUploadError('No image file was provided.')

    if size > max_upload_bytes():
        max_mb = max_upload_bytes() // (1024 * 1024)
        raise CustomBannerUploadError(f'Image too large (max {max_mb} MB).')

    mime = (getattr(upload, 'content_type', '') or '').strip().lower()
    allowed = allowed_content_types()
    if mime not in allowed:
        raise CustomBannerUploadError(
            'Unsupported file type. Upload a JPEG, PNG, or WebP image.',
        )
    return mime, _EXT_BY_MIME[mime]


def upload_custom_banner(content_type, content_id, upload):
    """Validate ``upload`` and store it as the record's custom banner.

    Uploads the file to ``custom-banners/<content_type>/<id>-<uuid>.<ext>``
    in ``AWS_S3_CONTENT_BUCKET`` and returns the public CDN URL. The caller
    is responsible for persisting that URL to ``custom_banner_url`` and for
    deleting any previous object via :func:`safe_delete_custom_banner`.

    Raises :class:`CustomBannerUploadError` on validation failure, missing
    config, or an S3 error — none of which mutate any state, so the caller
    can flash the message and leave the record untouched.
    """
    if content_type not in SUPPORTED_CONTENT_TYPES:
        raise CustomBannerUploadError(
            f'Unsupported content type: {content_type!r}.',
        )
    if not is_upload_enabled():
        raise CustomBannerUploadError(
            'Custom banner upload is not configured. Add the content CDN '
            'base and S3 bucket under Studio > Settings > Content Tools '
            'first.',
        )

    _mime, ext = _validate(upload)
    mime = _mime
    key = f'{key_prefix()}/{content_type}/{content_id}-{uuid.uuid4().hex}.{ext}'
    bucket = (get_config('AWS_S3_CONTENT_BUCKET', '') or '').strip()

    try:
        upload.seek(0)
    except (AttributeError, OSError):
        pass

    try:
        client = _s3_client()
        client.upload_fileobj(
            upload,
            bucket,
            key,
            ExtraArgs={
                'ContentType': mime,
                'CacheControl': 'public, max-age=86400',
            },
        )
    except (BotoCoreError, ClientError, boto3.exceptions.S3UploadFailedError) as exc:
        logger.warning(
            'upload_custom_banner: failed to upload %s/%s: %s',
            content_type, content_id, exc,
        )
        raise CustomBannerUploadError(
            'Upload failed while saving to storage. Please try again.',
        ) from exc

    url = cdn_url_for_key(key)
    if not url:
        # is_upload_enabled() already verified CONTENT_CDN_BASE, so this is
        # defensive — treat a missing URL as a config failure.
        raise CustomBannerUploadError(
            'Custom banner upload is not configured (CDN base missing).',
        )
    return url


def _custom_banner_key_from_url(content_type, url):
    """Return the safe-to-delete custom-banner S3 key encoded in ``url``.

    Cleanup is intentionally narrow (mirrors
    ``tasks._generated_banner_key_from_url``): only URLs under the configured
    CDN base and under ``<prefix>/<supported_content_type>/`` are eligible.
    Returns ``''`` for anything else so we never delete a frontmatter cover
    or an arbitrary URL an operator might have pasted in.
    """
    if content_type not in SUPPORTED_CONTENT_TYPES or not url:
        return ''

    cdn_base = (get_config('CONTENT_CDN_BASE', '') or '').rstrip('/')
    if not cdn_base:
        return ''

    expected_prefix = f'{key_prefix()}/{content_type}/'
    normalized_url = str(url).strip()
    normalized_base = cdn_base + '/'
    if not normalized_url.startswith(normalized_base):
        return ''

    key = normalized_url[len(normalized_base):].lstrip('/')
    if not key.startswith(expected_prefix):
        return ''
    parsed_key = urlparse(key).path.lstrip('/')
    if parsed_key != key or not parsed_key.startswith(expected_prefix):
        return ''
    if unquote(parsed_key) != parsed_key:
        return ''
    if any(segment in ('', '.', '..') for segment in parsed_key.split('/')):
        return ''
    return parsed_key


def safe_delete_custom_banner(content_type, url):
    """Best-effort delete of a previously uploaded custom-banner object.

    Returns True only when a safe ``custom-banners/<type>/`` key under the
    CDN base was deleted. Returns False for non-custom URLs, missing config,
    or S3 errors so a re-upload or remove never fails because cleanup
    failed.
    """
    key = _custom_banner_key_from_url(content_type, url)
    if not key:
        return False

    bucket = (get_config('AWS_S3_CONTENT_BUCKET', '') or '').strip()
    if not bucket:
        logger.warning(
            'safe_delete_custom_banner: bucket unset; skipping %s', key,
        )
        return False

    try:
        client = _s3_client()
        client.delete_object(Bucket=bucket, Key=key)
    except (BotoCoreError, ClientError) as exc:
        logger.warning(
            'safe_delete_custom_banner: failed to delete %s: %s', key, exc,
        )
        return False
    return True
