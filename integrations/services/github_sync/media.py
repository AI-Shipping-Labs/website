"""Image URL rewriting and S3 media upload helpers."""

import hashlib
import mimetypes
import os
import re

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings

from integrations.config import get_config
from integrations.services.github_sync.common import IMAGE_EXTENSIONS, logger


def _image_base_url(repo_name):
    """Return the public base URL and whether it expects a repo prefix."""
    cdn_base = get_config('CONTENT_CDN_BASE', '')
    if cdn_base:
        return cdn_base.rstrip('/'), True
    return f'https://raw.githubusercontent.com/{repo_name}/main', False


def _repo_short(repo_name):
    return repo_name.split('/')[-1] if '/' in repo_name else repo_name


def _resolve_image_path(path, base_path=''):
    """Resolve an author image path to the storage path used by sync."""
    clean_path = path.lstrip('/')
    if path.startswith('/'):
        # Static-site content commonly references files under public/ as
        # root-relative URLs. The S3 uploader preserves repo paths, so map
        # /images/foo.png to public/images/foo.png.
        if clean_path.startswith('images/'):
            return os.path.normpath(os.path.join('public', clean_path))
        return os.path.normpath(clean_path)
    return os.path.normpath(os.path.join(base_path, clean_path))


def rewrite_image_urls(markdown_text, repo_name, base_path=''):
    """Rewrite relative image URLs in markdown and HTML to absolute storage URLs.

    Args:
        markdown_text: Markdown content with relative image paths.
        repo_name: Repo name for the storage path prefix.
        base_path: Base path within the repo for resolving relative paths.

    Returns:
        str: Markdown with rewritten image URLs.
    """
    image_base, include_repo_prefix = _image_base_url(repo_name)
    repo_short = _repo_short(repo_name)

    def _rewrite_path(path):
        """Rewrite a single image path if it's relative."""
        if path.startswith(('http://', 'https://', 'data:')):
            return path
        full_path = _resolve_image_path(path, base_path)
        if include_repo_prefix:
            return f'{image_base}/{repo_short}/{full_path}'
        return f'{image_base}/{full_path}'

    def replace_md_image(match):
        alt = match.group(1)
        path = match.group(2)
        return f'![{alt}]({_rewrite_path(path)})'

    def replace_html_image(match):
        prefix = match.group(1)
        path = match.group(2)
        suffix = match.group(3)
        return f'{prefix}{_rewrite_path(path)}{suffix}'

    # Rewrite markdown image syntax: ![alt](path)
    md_pattern = r'!\[([^\]]*)\]\(([^)]+)\)'
    result = re.sub(md_pattern, replace_md_image, markdown_text)

    # Rewrite HTML img src: <img src="path" or <img src='path'
    html_pattern = r'(<img\s[^>]*?src=["\'])([^"\']+)(["\'])'
    result = re.sub(html_pattern, replace_html_image, result)

    return result


def rewrite_cover_image_url(cover_image, source, rel_path):
    """Rewrite a cover_image path from frontmatter to a CDN URL if relative.

    Args:
        cover_image: The cover_image value from frontmatter (may be empty,
            a relative path, or a full URL).
        source: ContentSource instance (used for repo_name).
        rel_path: Relative path of the content file within the repo
            (used to resolve relative image paths).

    Returns:
        str: The CDN URL if the path was relative, the original URL if absolute,
            or empty string if no cover image.
    """
    if not cover_image:
        return ''
    if cover_image.startswith(('http://', 'https://')):
        return cover_image

    base_dir = os.path.dirname(rel_path)
    full_path = _resolve_image_path(cover_image, base_dir)
    image_base, include_repo_prefix = _image_base_url(source.repo_name)
    if include_repo_prefix:
        return f'{image_base}/{source.short_name}/{full_path}'
    return f'{image_base}/{full_path}'


def _md5_file(filepath, chunk_size=8192):
    """Compute the MD5 hex digest of a file."""
    md5 = hashlib.md5()
    with open(filepath, 'rb') as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            md5.update(chunk)
    return md5.hexdigest()


def upload_images_to_s3(content_dir, source):
    """Upload image files from a content directory to S3.

    Walks the content directory for image files and uploads them to S3,
    skipping files whose MD5 matches the existing S3 ETag.

    Args:
        content_dir: Local directory containing content files and images.
        source: ContentSource instance (used for the S3 key prefix).

    Returns:
        dict: {'uploaded': int, 'skipped': int, 'errors': list}
    """
    # Issue #532: kill-switch for tests / local dev. When S3_ENABLED is False
    # (the default everywhere except production), short-circuit before
    # constructing any boto3 client so we never make a real ``list_objects_v2``
    # round-trip against AWS. Each round-trip is ~300ms-1s with the
    # "non-empty Access Key" warning that the legacy code path emitted, and
    # integration tests exercise this function many times per file.
    if not getattr(settings, 'S3_ENABLED', False):
        logger.info(
            'S3_ENABLED is false - skipping image upload for %s',
            source.repo_name,
        )
        return {'uploaded': 0, 'skipped': 0, 'errors': []}

    bucket = get_config('AWS_S3_CONTENT_BUCKET')
    region = get_config('AWS_S3_CONTENT_REGION', 'eu-central-1')

    if not bucket:
        logger.info('AWS_S3_CONTENT_BUCKET not configured, skipping image upload')
        return {'uploaded': 0, 'skipped': 0, 'errors': []}

    repo_short = source.repo_name.split('/')[-1] if '/' in source.repo_name else source.repo_name
    stats = {'uploaded': 0, 'skipped': 0, 'errors': []}

    try:
        s3 = boto3.client(
            's3',
            region_name=region,
            aws_access_key_id=get_config('AWS_ACCESS_KEY_ID'),
            aws_secret_access_key=get_config('AWS_SECRET_ACCESS_KEY'),
        )
    except (BotoCoreError, ClientError) as e:
        logger.warning('Failed to create S3 client: %s', e)
        return {'uploaded': 0, 'skipped': 0, 'errors': [{'file': '', 'error': str(e)}]}

    # Build index of existing S3 ETags (MD5 for single-part uploads)
    s3_prefix = f'{repo_short}/'
    existing_etags = {}
    try:
        paginator = s3.get_paginator('list_objects_v2')
        for page in paginator.paginate(Bucket=bucket, Prefix=s3_prefix):
            for obj in page.get('Contents', []):
                # ETag is quoted, e.g. '"d41d8cd98f00b204e9800998ecf8427e"'
                existing_etags[obj['Key']] = obj['ETag'].strip('"')
    except (BotoCoreError, ClientError) as e:
        logger.warning('Failed to list S3 objects: %s', e)

    for root, dirs, files in os.walk(content_dir):
        # Skip .git directory
        if '.git' in root:
            continue
        for filename in files:
            ext = os.path.splitext(filename)[1].lower()
            if ext not in IMAGE_EXTENSIONS:
                continue

            filepath = os.path.join(root, filename)
            rel_path = os.path.relpath(filepath, content_dir)
            s3_key = f'{repo_short}/{rel_path}'

            # Compute local MD5 and compare against S3 ETag
            local_md5 = _md5_file(filepath)
            if s3_key in existing_etags and existing_etags[s3_key] == local_md5:
                stats['skipped'] += 1
                continue

            try:
                content_type = mimetypes.guess_type(filename)[0] or 'application/octet-stream'
                s3.upload_file(
                    filepath, bucket, s3_key,
                    ExtraArgs={
                        'ContentType': content_type,
                        'CacheControl': 'public, max-age=86400',
                    },
                )
                stats['uploaded'] += 1
            except (BotoCoreError, ClientError, boto3.exceptions.S3UploadFailedError) as e:
                stats['errors'].append({'file': rel_path, 'error': str(e)})
                logger.warning('Failed to upload %s to S3: %s', rel_path, e)

    logger.info(
        'S3 image upload for %s: %d uploaded, %d skipped, %d errors',
        source.repo_name, stats['uploaded'], stats['skipped'], len(stats['errors']),
    )
    return stats

def _collect_image_paths(content_dir):
    """Return a set of all image file paths relative to content_dir."""
    image_paths = set()
    for root, dirs, files in os.walk(content_dir):
        if '.git' in root:
            continue
        for filename in files:
            ext = os.path.splitext(filename)[1].lower()
            if ext in IMAGE_EXTENSIONS:
                filepath = os.path.join(root, filename)
                rel_path = os.path.relpath(filepath, content_dir)
                image_paths.add(rel_path)
    return image_paths

def _check_broken_image_refs(body, rel_path, repo_name, base_dir, known_images, errors):
    """Check for broken image references in markdown content.

    Logs warnings for images not found in the repo.
    """
    # Match markdown image syntax: ![alt](path)
    md_pattern = r'!\[([^\]]*)\]\(([^)]+)\)'
    for match in re.finditer(md_pattern, body):
        img_path = match.group(2)
        if img_path.startswith(('http://', 'https://')):
            continue
        resolved = _resolve_image_path(img_path, base_dir)
        if resolved not in known_images:
            errors.append({
                'file': rel_path,
                'error': f'Broken image reference: {img_path} not found in repo',
            })
            logger.warning(
                'Broken image reference in %s: %s not found in repo',
                rel_path, img_path,
            )
