"""Shared validation for private downloadable asset metadata."""

from urllib.parse import unquote, urlparse

from content.access import LEVEL_BASIC, LEVEL_MAIN, LEVEL_OPEN, LEVEL_PREMIUM
from content.models.download import (
    DOWNLOAD_EXTENSION_MIME_TYPES,
    SAFE_DOWNLOAD_FILE_TYPES,
)

SAFE_EXTENSIONS = {
    'pdf': {'.pdf'},
    'zip': {'.zip'},
    'slides': {'.ppt', '.pptx'},
    'notebook': {'.ipynb'},
    'csv': {'.csv'},
}
SAFE_LEVELS = {LEVEL_OPEN, LEVEL_BASIC, LEVEL_MAIN, LEVEL_PREMIUM}


class DownloadMetadataError(ValueError):
    pass


def _fully_unquote(value):
    """Decode nested URL encoding before applying path policy."""
    current = str(value or '').strip()
    for _ in range(6):
        decoded = unquote(current)
        if decoded == current:
            return decoded
        current = decoded
    raise DownloadMetadataError('storage_key is encoded too deeply')


def normalize_storage_key(value):
    key = _fully_unquote(value)
    if not key:
        raise DownloadMetadataError('storage_key is required')
    if (
        key.startswith('/')
        or '\\' in key
        or '%' in key
        or any(ord(c) < 32 for c in key)
    ):
        raise DownloadMetadataError('storage_key contains unsafe characters')
    parts = key.split('/')
    if any(part in {'', '.', '..'} for part in parts):
        raise DownloadMetadataError('storage_key contains an unsafe path segment')
    if not key.startswith('downloads/'):
        raise DownloadMetadataError('storage_key must start with downloads/')
    return key


def validate_download_metadata(*, storage_key, file_type, file_size_bytes,
                               required_level, asset_mime_type=''):
    key = normalize_storage_key(storage_key)
    if file_type not in SAFE_DOWNLOAD_FILE_TYPES:
        raise DownloadMetadataError(f'file_type {file_type!r} is not publishable')
    try:
        size = int(file_size_bytes)
    except (TypeError, ValueError) as exc:
        raise DownloadMetadataError('file_size_bytes must be an integer') from exc
    if size <= 0:
        raise DownloadMetadataError('file_size_bytes must be greater than zero')
    try:
        level = int(required_level)
    except (TypeError, ValueError) as exc:
        raise DownloadMetadataError('required_level must be an integer') from exc
    if level not in SAFE_LEVELS:
        raise DownloadMetadataError('required_level must be 0, 10, 20, or 30')
    extension = '.' + key.rsplit('.', 1)[-1].lower() if '.' in key else ''
    if extension not in SAFE_EXTENSIONS[file_type]:
        raise DownloadMetadataError(
            f'storage_key extension does not match file_type {file_type!r}',
        )
    expected_mime = DOWNLOAD_EXTENSION_MIME_TYPES[file_type][extension]
    if asset_mime_type and asset_mime_type != expected_mime:
        raise DownloadMetadataError('asset_mime_type does not match file_type')
    return {
        'storage_key': key,
        'file_type': file_type,
        'file_size_bytes': size,
        'required_level': level,
        'asset_mime_type': asset_mime_type or expected_mime,
    }


def storage_key_from_configured_s3_url(url, bucket, region):
    """Extract only a canonical configured-bucket virtual-host S3 URL."""
    try:
        parsed = urlparse(str(url or '').strip())
        scheme = parsed.scheme
        hostname = parsed.hostname
        path = parsed.path
        query = parsed.query
        fragment = parsed.fragment
        username = parsed.username
        password = parsed.password
        port = parsed.port
    except Exception as exc:
        # ``urlparse`` and its lazy properties (notably ``hostname`` and
        # ``port``) can reject malformed bracketed hosts or ports. Normalize
        # every such parser failure so a single legacy row cannot abort an
        # audit/backfill run.
        raise DownloadMetadataError('URL is malformed') from exc
    expected_host = f'{bucket}.s3.{region}.amazonaws.com'
    if (
        scheme != 'https'
        or hostname != expected_host
        or not path.startswith('/downloads/')
        or query
        or fragment
        or username
        or password
        or port
    ):
        raise DownloadMetadataError('URL is not in the configured S3 bucket')
    return normalize_storage_key(path[1:])
