"""GitHub integration service for content sync.

Handles:
- Webhook signature validation (X-Hub-Signature-256)
- GitHub App authentication for private repos
- Repository cloning/pulling
- Content sync: parse markdown/YAML, upload images, upsert content
"""

import hashlib
import hmac
import logging
import mimetypes
import os
import re
import shutil
import subprocess
import tempfile
import time

import boto3
import frontmatter
import jwt
import requests
import yaml
from django.conf import settings
from django.utils import timezone

from integrations.models import ContentSource, SyncLog

logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.ico'}
CONTENT_EXTENSIONS = {'.md', '.yaml', '.yml'}

GITHUB_API_BASE = 'https://api.github.com'

# Required frontmatter fields per content type
REQUIRED_FIELDS = {
    'article': ['title'],
    'course': ['title'],
    'module': ['title'],
    'unit': ['title'],
    'recording': ['title', 'video_url'],
    'project': ['title'],
    'curated_link': ['title', 'url', 'item_id'],
    'download': ['title'],
}

# Sync lock timeout in minutes
SYNC_LOCK_TIMEOUT_MINUTES = 10


def extract_sort_order(name):
    """Extract numeric prefix from a filename or directory name.
    '01-day-1' -> 1, '02-setup.md' -> 2, 'intro.md' -> 0
    """
    match = re.match(r'^(\d+)', name)
    return int(match.group(1)) if match else 0


def derive_slug(name):
    """Derive slug from filename/dirname, stripping numeric prefix.
    '01-day-1' -> 'day-1'
    '02-environment.md' -> 'environment'
    'lesson.md' -> 'lesson'
    """
    stem = name.rsplit('.', 1)[0] if '.' in name else name
    match = re.match(r'^\d+-(.+)', stem)
    return match.group(1) if match else stem


class GitHubSyncError(Exception):
    """Raised when a GitHub sync operation fails."""
    pass


def validate_webhook_signature(request, secret):
    """Validate a GitHub webhook request using X-Hub-Signature-256.

    Args:
        request: Django HttpRequest object.
        secret: The webhook secret string.

    Returns:
        bool: True if the signature is valid.
    """
    if not secret:
        logger.warning('GitHub webhook secret not configured')
        return False

    signature_header = request.headers.get('X-Hub-Signature-256', '')
    if not signature_header:
        return False

    expected_sig = 'sha256=' + hmac.new(
        secret.encode('utf-8'),
        request.body,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected_sig, signature_header)


def find_content_source(repo_full_name):
    """Find ContentSource(s) by repo name.

    Args:
        repo_full_name: Full repo name (e.g. "AI-Shipping-Labs/content").

    Returns:
        ContentSource queryset (may contain multiple sources for monorepo).
    """
    return ContentSource.objects.filter(repo_name=repo_full_name)


def find_content_source_single(repo_full_name):
    """Find a single ContentSource by repo name (legacy, for backward compat).

    Args:
        repo_full_name: Full repo name.

    Returns:
        ContentSource or None.
    """
    try:
        return ContentSource.objects.filter(repo_name=repo_full_name).first()
    except ContentSource.DoesNotExist:
        return None


def generate_github_app_token():
    """Generate a GitHub App installation access token for private repo access.

    Uses GITHUB_APP_ID and GITHUB_APP_PRIVATE_KEY from settings.

    Returns:
        str: Installation access token.

    Raises:
        GitHubSyncError: If credentials are missing or token generation fails.
    """
    app_id = getattr(settings, 'GITHUB_APP_ID', '')
    private_key = getattr(settings, 'GITHUB_APP_PRIVATE_KEY', '')
    installation_id = getattr(settings, 'GITHUB_APP_INSTALLATION_ID', '')

    if not all([app_id, private_key, installation_id]):
        raise GitHubSyncError(
            'GitHub App credentials not configured. '
            'Set GITHUB_APP_ID, GITHUB_APP_PRIVATE_KEY, '
            'and GITHUB_APP_INSTALLATION_ID.'
        )

    now = int(time.time())
    payload = {
        'iat': now - 60,
        'exp': now + (10 * 60),
        'iss': app_id,
    }

    encoded_jwt = jwt.encode(payload, private_key, algorithm='RS256')

    response = requests.post(
        f'{GITHUB_API_BASE}/app/installations/{installation_id}/access_tokens',
        headers={
            'Authorization': f'Bearer {encoded_jwt}',
            'Accept': 'application/vnd.github+json',
        },
        timeout=10,
    )

    if response.status_code != 201:
        raise GitHubSyncError(
            f'Failed to get GitHub installation token: {response.status_code} '
            f'{response.text}'
        )

    return response.json()['token']


def clone_or_pull_repo(repo_name, target_dir, is_private=False):
    """Clone or pull a GitHub repository to a local directory.

    Args:
        repo_name: Full repo name (e.g. "AI-Shipping-Labs/blog").
        target_dir: Local directory to clone into.
        is_private: Whether the repo requires authentication.

    Returns:
        str: The HEAD commit SHA.

    Raises:
        GitHubSyncError: If clone/pull fails.
    """
    if is_private:
        token = generate_github_app_token()
        repo_url = f'https://x-access-token:{token}@github.com/{repo_name}.git'
    else:
        repo_url = f'https://github.com/{repo_name}.git'

    clone_timeout = getattr(settings, 'GITHUB_SYNC_CLONE_TIMEOUT', 300)

    try:
        if os.path.exists(os.path.join(target_dir, '.git')):
            # Pull existing repo
            result = subprocess.run(
                ['git', 'pull', '--ff-only'],
                cwd=target_dir,
                capture_output=True,
                text=True,
                timeout=clone_timeout,
            )
        else:
            # Clone fresh
            result = subprocess.run(
                ['git', 'clone', '--depth', '1', repo_url, target_dir],
                capture_output=True,
                text=True,
                timeout=clone_timeout,
            )

        if result.returncode != 0:
            raise GitHubSyncError(
                f'Git operation failed: {result.stderr}'
            )

        # Get the HEAD commit SHA
        sha_result = subprocess.run(
            ['git', 'rev-parse', 'HEAD'],
            cwd=target_dir,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return sha_result.stdout.strip()

    except subprocess.TimeoutExpired:
        raise GitHubSyncError('Git operation timed out')
    except FileNotFoundError:
        raise GitHubSyncError('git command not found')


def rewrite_image_urls(markdown_text, repo_name, base_path=''):
    """Rewrite relative image URLs in markdown and HTML to absolute storage URLs.

    Args:
        markdown_text: Markdown content with relative image paths.
        repo_name: Repo name for the storage path prefix.
        base_path: Base path within the repo for resolving relative paths.

    Returns:
        str: Markdown with rewritten image URLs.
    """
    cdn_base = getattr(settings, 'CONTENT_CDN_BASE', '/static/content-images')
    repo_short = repo_name.split('/')[-1] if '/' in repo_name else repo_name

    def _rewrite_path(path):
        """Rewrite a single image path if it's relative."""
        if path.startswith(('http://', 'https://')):
            return path
        # Strip leading slash for absolute-within-repo paths
        clean_path = path.lstrip('/')
        full_path = os.path.normpath(os.path.join(base_path, clean_path))
        return f'{cdn_base}/{repo_short}/{full_path}'

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
    bucket = getattr(settings, 'AWS_S3_CONTENT_BUCKET', '')
    region = getattr(settings, 'AWS_S3_CONTENT_REGION', 'eu-central-1')

    if not bucket:
        logger.info('AWS_S3_CONTENT_BUCKET not configured, skipping image upload')
        return {'uploaded': 0, 'skipped': 0, 'errors': []}

    repo_short = source.repo_name.split('/')[-1] if '/' in source.repo_name else source.repo_name
    stats = {'uploaded': 0, 'skipped': 0, 'errors': []}

    try:
        s3 = boto3.client(
            's3',
            region_name=region,
            aws_access_key_id=getattr(settings, 'AWS_ACCESS_KEY_ID', ''),
            aws_secret_access_key=getattr(settings, 'AWS_SECRET_ACCESS_KEY', ''),
        )
    except Exception as e:
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
    except Exception as e:
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
            except Exception as e:
                stats['errors'].append({'file': rel_path, 'error': str(e)})
                logger.warning('Failed to upload %s to S3: %s', rel_path, e)

    logger.info(
        'S3 image upload for %s: %d uploaded, %d skipped, %d errors',
        source.repo_name, stats['uploaded'], stats['skipped'], len(stats['errors']),
    )
    return stats


def sync_content_source(source, repo_dir=None):
    """Sync content from a GitHub repo into the database.

    This is the main sync function that:
    1. Clones/pulls the repo
    2. Parses all content files
    3. Upserts content into the database
    4. Soft-deletes content no longer in the repo
    5. Logs the result

    Args:
        source: ContentSource instance.
        repo_dir: Optional pre-cloned repo directory (for testing).

    Returns:
        SyncLog: The sync log entry.
    """
    # Acquire sync lock (Edge Case 4: Concurrent Syncs)
    # Skip locking when repo_dir is provided (testing mode)
    use_lock = repo_dir is None
    if use_lock and not acquire_sync_lock(source):
        logger.info(
            'Sync already in progress for %s, skipping.', source.repo_name,
        )
        sync_log = SyncLog.objects.create(
            source=source,
            status='skipped',
            finished_at=timezone.now(),
            errors=[{'file': '', 'error': 'Sync already in progress, skipped.'}],
        )
        return sync_log

    sync_log = SyncLog.objects.create(
        source=source,
        status='running',
    )

    source.last_sync_status = 'running'
    source.save(update_fields=['last_sync_status', 'updated_at'])

    temp_dir = None
    try:
        if repo_dir is None:
            temp_dir = tempfile.mkdtemp(prefix='github-sync-')
            commit_sha = clone_or_pull_repo(
                source.repo_name, temp_dir, source.is_private,
            )
            repo_dir = temp_dir
        else:
            # For testing, use provided directory
            commit_sha = 'test-commit-sha'

        # Resolve content subdirectory
        content_dir = repo_dir
        if source.content_path:
            content_dir = os.path.join(repo_dir, source.content_path)
            if not os.path.isdir(content_dir):
                raise GitHubSyncError(
                    f'Content path {source.content_path!r} not found in repo'
                )

        # Edge Case 5: Max files guard
        file_count = _count_content_files(content_dir)
        if file_count > source.max_files:
            raise GitHubSyncError(
                f'Repository contains more than {source.max_files} content files. '
                f'Increase max_files on the ContentSource or reduce repo size.'
            )

        # Upload images to S3 before content sync (so CDN URLs are live)
        # Edge Case 3: S3 errors do not abort sync
        s3_stats = upload_images_to_s3(content_dir, source)
        s3_errors = s3_stats.get('errors', [])
        if s3_errors:
            logger.warning(
                'S3 image upload had %d error(s) for %s, continuing with content sync.',
                len(s3_errors), source.repo_name,
            )
        logger.info(
            'S3 upload for %s: %d uploaded, %d skipped',
            source.repo_name, s3_stats['uploaded'], s3_stats['skipped'],
        )

        # Collect known image paths for broken reference checks (Edge Case 8)
        known_images = _collect_image_paths(content_dir)

        # Dispatch to content-type-specific sync
        sync_func = _get_sync_function(source.content_type)
        stats = sync_func(source, content_dir, commit_sha, sync_log,
                          known_images=known_images)

        # Merge S3 errors into stats
        all_errors = s3_errors + stats.get('errors', [])

        # Update sync log
        sync_log.items_created = stats.get('created', 0)
        sync_log.items_updated = stats.get('updated', 0)
        sync_log.items_deleted = stats.get('deleted', 0)
        sync_log.errors = all_errors

        if sync_log.errors:
            sync_log.status = 'partial'
        else:
            sync_log.status = 'success'

        sync_log.finished_at = timezone.now()
        sync_log.save()

        # Update source
        source.last_synced_at = timezone.now()
        source.last_sync_status = sync_log.status
        source.last_sync_log = (
            f"Created: {sync_log.items_created}, "
            f"Updated: {sync_log.items_updated}, "
            f"Deleted: {sync_log.items_deleted}"
        )
        if sync_log.errors:
            source.last_sync_log += f"\nErrors: {len(sync_log.errors)}"
        source.save()

    except Exception as e:
        logger.exception('Sync failed for %s', source.repo_name)
        sync_log.status = 'failed'
        sync_log.finished_at = timezone.now()
        sync_log.errors = [{'file': '', 'error': str(e)}]
        sync_log.save()

        source.last_sync_status = 'failed'
        source.last_sync_log = f'Sync failed: {e}'
        source.save()

    finally:
        if temp_dir and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)

        # Release sync lock and check for follow-up (Edge Case 9)
        if use_lock:
            follow_up = release_sync_lock(source)
            if follow_up:
                logger.info(
                    'Follow-up sync requested for %s, enqueuing.',
                    source.repo_name,
                )
                try:
                    from django_q.tasks import async_task
                    async_task(
                        'integrations.services.github.sync_content_source',
                        source,
                        task_name=f'sync-{source.repo_name}-{source.content_type}-followup',
                    )
                except ImportError:
                    sync_content_source(source)

    return sync_log


def _get_sync_function(content_type):
    """Return the sync function for a given content type."""
    sync_functions = {
        'article': _sync_articles,
        'course': _sync_courses,
        'resource': _sync_resources,
        'project': _sync_projects,
        'interview_question': _sync_interview_questions,
    }
    func = sync_functions.get(content_type)
    if not func:
        raise GitHubSyncError(f'Unknown content type: {content_type}')
    return func


def _parse_markdown_file(filepath):
    """Parse a markdown file with YAML frontmatter.

    Args:
        filepath: Path to the markdown file.

    Returns:
        tuple: (metadata dict, body string)
    """
    post = frontmatter.load(filepath)
    return dict(post.metadata), post.content


def _parse_yaml_file(filepath):
    """Parse a YAML file.

    Args:
        filepath: Path to the YAML file.

    Returns:
        dict: Parsed YAML data.
    """
    with open(filepath, 'r') as f:
        return yaml.safe_load(f) or {}


def _validate_frontmatter(metadata, content_type, filepath):
    """Validate that required frontmatter fields are present.

    Args:
        metadata: Parsed frontmatter dict.
        content_type: Content type key from REQUIRED_FIELDS.
        filepath: File path for error messages.

    Raises:
        ValueError: If required fields are missing.
    """
    required = REQUIRED_FIELDS.get(content_type, [])
    missing = [f for f in required if not metadata.get(f)]
    if missing:
        raise ValueError(
            f"Missing required field(s) in {filepath}: {', '.join(missing)}"
        )


def _check_slug_collision(model_class, slug, source_repo, filepath):
    """Check for slug collision with a different source.

    Returns True if a collision exists (file should be skipped).
    Returns False if no collision.
    """
    existing = model_class.objects.filter(slug=slug).exclude(
        source_repo=source_repo,
    ).first()
    if existing:
        other_source = existing.source_repo or 'studio'
        logger.warning(
            "Slug collision: '%s' already exists from source '%s' "
            "(source_repo=%s). Skipped %s.",
            slug, other_source, existing.source_repo, filepath,
        )
        return True
    return False


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


def _compute_content_hash(text):
    """Compute MD5 hex digest of text for rename detection."""
    return hashlib.md5(text.encode('utf-8')).hexdigest()


def _count_content_files(content_dir):
    """Count content files (.md, .yaml, .yml) in a directory tree."""
    count = 0
    for root, dirs, files in os.walk(content_dir):
        if '.git' in root:
            continue
        for filename in files:
            ext = os.path.splitext(filename)[1].lower()
            if ext in CONTENT_EXTENSIONS:
                count += 1
            elif ext not in IMAGE_EXTENSIONS and filename != 'README.md':
                logger.debug(
                    'Skipping non-content, non-image file: %s',
                    os.path.join(root, filename),
                )
    return count


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
        clean_path = img_path.lstrip('/')
        resolved = os.path.normpath(os.path.join(base_dir, clean_path))
        if resolved not in known_images:
            errors.append({
                'file': rel_path,
                'error': f'Broken image reference: {img_path} not found in repo',
            })
            logger.warning(
                'Broken image reference in %s: %s not found in repo',
                rel_path, img_path,
            )


def acquire_sync_lock(source):
    """Attempt to acquire the sync lock for a ContentSource.

    Uses atomic queryset UPDATE to prevent race conditions. A lock older than
    SYNC_LOCK_TIMEOUT_MINUTES is considered stale and can be reclaimed.

    Returns:
        bool: True if lock was acquired, False if already locked.
    """
    from django.db.models import Q

    now = timezone.now()
    stale_threshold = now - timezone.timedelta(minutes=SYNC_LOCK_TIMEOUT_MINUTES)

    updated = ContentSource.objects.filter(
        pk=source.pk,
    ).filter(
        Q(sync_locked_at__isnull=True) | Q(sync_locked_at__lt=stale_threshold),
    ).update(sync_locked_at=now)

    if updated == 0:
        return False

    source.refresh_from_db()
    return True


def release_sync_lock(source):
    """Release the sync lock and check for pending sync requests.

    Returns:
        bool: True if a follow-up sync was requested.
    """
    source.refresh_from_db()
    was_requested = source.sync_requested
    source.sync_locked_at = None
    source.sync_requested = False
    source.save(update_fields=['sync_locked_at', 'sync_requested', 'updated_at'])
    return was_requested


def _sync_articles(source, repo_dir, commit_sha, sync_log, known_images=None):
    """Sync blog articles from the repo."""
    from content.models import Article

    stats = {'created': 0, 'updated': 0, 'deleted': 0, 'errors': []}
    seen_slugs = set()
    failed_slugs = set()

    # Find all .md files (excluding README)
    for root, dirs, files in os.walk(repo_dir):
        # Skip .git directory
        if '.git' in root:
            continue
        for filename in files:
            if not filename.endswith('.md') or filename.upper() == 'README.MD':
                continue

            filepath = os.path.join(root, filename)
            rel_path = os.path.relpath(filepath, repo_dir)
            current_slug = None  # Track slug for error handling

            try:
                metadata, body = _parse_markdown_file(filepath)

                # Derive slug before validation so we can track failures
                current_slug = metadata.get('slug', os.path.splitext(filename)[0])

                # Edge Case 7: Frontmatter validation
                _validate_frontmatter(metadata, 'article', rel_path)

                # Require content_id in frontmatter
                content_id = metadata.get('content_id')
                if not content_id:
                    msg = f'Skipping {rel_path}: missing content_id in frontmatter'
                    logger.warning(msg)
                    stats['errors'].append({'file': rel_path, 'error': msg})
                    continue

                # Edge Case 2: Check slug collision across sources
                if _check_slug_collision(Article, current_slug, source.repo_name, rel_path):
                    stats['errors'].append({
                        'file': rel_path,
                        'error': (
                            f"Slug collision: '{current_slug}' already exists from a "
                            f"different source. Skipped."
                        ),
                    })
                    failed_slugs.add(current_slug)
                    continue

                # Warn on same-source slug collision (last-file-wins)
                if current_slug in seen_slugs:
                    logger.warning(
                        'Same-source slug collision: %s appears multiple '
                        'times in %s. Last file wins.',
                        current_slug, source.repo_name,
                    )

                seen_slugs.add(current_slug)

                # Rewrite image URLs
                base_dir = os.path.dirname(rel_path)

                # Edge Case 8: Check for broken image references
                if known_images is not None:
                    _check_broken_image_refs(
                        body, rel_path, source.repo_name, base_dir,
                        known_images, stats['errors'],
                    )

                body = rewrite_image_urls(body, source.repo_name, base_dir)

                # Extract page_type and data from frontmatter
                page_type = metadata.get('page_type', 'blog')
                data = metadata.get('data', {})

                defaults = {
                    'title': metadata.get('title', current_slug),
                    'description': metadata.get('description', ''),
                    'content_markdown': body,
                    'author': metadata.get('author', ''),
                    'tags': metadata.get('tags', []),
                    'cover_image_url': metadata.get('cover_image', ''),
                    'required_level': metadata.get('required_level', 0),
                    'published': True,
                    'source_repo': source.repo_name,
                    'source_path': rel_path,
                    'source_commit': commit_sha,
                    'page_type': page_type,
                    'data_json': data,
                    'content_id': content_id,
                }

                # Parse date
                date_str = metadata.get('date')
                if date_str:
                    from datetime import date as date_type
                    if isinstance(date_str, str):
                        defaults['date'] = date_type.fromisoformat(date_str)
                    elif isinstance(date_str, date_type):
                        defaults['date'] = date_str
                else:
                    defaults['date'] = timezone.now().date()

                # Edge Case 2: Scope update_or_create to source_repo
                article, created = Article.objects.update_or_create(
                    slug=current_slug,
                    source_repo=source.repo_name,
                    defaults=defaults,
                )

                # Expand widgets after save (save already rendered markdown to HTML)
                if article.data_json:
                    from content.utils.widgets import expand_widgets
                    expanded = expand_widgets(article.content_html, article.data_json)
                    Article.objects.filter(pk=article.pk).update(content_html=expanded)

                if created:
                    stats['created'] += 1
                else:
                    stats['updated'] += 1

            except Exception as e:
                # Track the slug as failed so it's excluded from cleanup.
                # Use filename-based slug as safe fallback, plus current_slug
                # if it was derived from metadata before the error.
                failed_slugs.add(os.path.splitext(filename)[0])
                if current_slug:
                    failed_slugs.add(current_slug)
                stats['errors'].append({
                    'file': rel_path,
                    'error': str(e),
                })
                logger.warning('Error syncing article %s: %s', rel_path, e)

    # Edge Case 3: Exclude failed_slugs from stale-content cleanup
    stale_articles = Article.objects.filter(
        source_repo=source.repo_name,
        published=True,
    ).exclude(slug__in=seen_slugs).exclude(slug__in=failed_slugs)

    deleted_count = stale_articles.count()
    stale_articles.update(published=False, status='draft')
    stats['deleted'] = deleted_count

    return stats


def _sync_courses(source, repo_dir, commit_sha, sync_log, known_images=None):
    """Sync courses with modules and units from the repo."""
    from content.models import Course, Module, Unit

    stats = {'created': 0, 'updated': 0, 'deleted': 0, 'errors': []}
    seen_course_slugs = set()
    failed_course_slugs = set()

    # Walk top-level directories (each is a course)
    for entry in os.scandir(repo_dir):
        if not entry.is_dir() or entry.name.startswith('.'):
            continue

        course_yaml_path = os.path.join(entry.path, 'course.yaml')
        if not os.path.exists(course_yaml_path):
            continue

        try:
            course_data = _parse_yaml_file(course_yaml_path)
            slug = course_data.get('slug', entry.name)
            rel_path = os.path.relpath(entry.path, repo_dir)

            # Edge Case 7: Frontmatter validation
            _validate_frontmatter(course_data, 'course', rel_path)

            # Require content_id in frontmatter
            course_content_id = course_data.get('content_id')
            if not course_content_id:
                msg = f'Skipping {rel_path}: missing content_id in frontmatter'
                logger.warning(msg)
                stats['errors'].append({'file': rel_path, 'error': msg})
                continue

            # Edge Case 2: Slug collision across sources
            if _check_slug_collision(Course, slug, source.repo_name, rel_path):
                stats['errors'].append({
                    'file': rel_path,
                    'error': (
                        f"Slug collision: '{slug}' already exists from a "
                        f"different source. Skipped."
                    ),
                })
                failed_course_slugs.add(slug)
                continue

            seen_course_slugs.add(slug)

            course_defaults = {
                'title': course_data.get('title', slug),
                'description': course_data.get('description', ''),
                'instructor_name': course_data.get('instructor_name', ''),
                'instructor_bio': course_data.get('instructor_bio', ''),
                'cover_image_url': course_data.get('cover_image', ''),
                'required_level': course_data.get('required_level', 0),
                'is_free': course_data.get('is_free', False),
                'discussion_url': course_data.get('discussion_url', ''),
                'tags': course_data.get('tags', []),
                'testimonials': course_data.get('testimonials', []),
                'status': 'published',
                'source_repo': source.repo_name,
                'source_path': rel_path,
                'source_commit': commit_sha,
                'content_id': course_content_id,
            }

            course, created = Course.objects.update_or_create(
                slug=slug,
                source_repo=source.repo_name,
                defaults=course_defaults,
            )
            if created:
                stats['created'] += 1
            else:
                stats['updated'] += 1

            # Sync modules
            _sync_course_modules(
                course, entry.path, repo_dir, source.repo_name,
                commit_sha, stats, known_images=known_images,
            )

        except Exception as e:
            try:
                failed_slug = course_data.get('slug', entry.name)
            except Exception:
                failed_slug = entry.name
            failed_course_slugs.add(failed_slug)
            stats['errors'].append({
                'file': os.path.relpath(course_yaml_path, repo_dir),
                'error': str(e),
            })
            logger.warning('Error syncing course %s: %s', entry.name, e)

    # Edge Case 3: Exclude failed slugs from stale-content cleanup
    stale_courses = Course.objects.filter(
        source_repo=source.repo_name,
        status='published',
    ).exclude(slug__in=seen_course_slugs).exclude(slug__in=failed_course_slugs)

    deleted_count = stale_courses.count()
    stale_courses.update(status='draft')
    stats['deleted'] += deleted_count

    return stats


def _sync_course_modules(course, course_dir, repo_dir, repo_name, commit_sha, stats,
                         known_images=None):
    """Sync modules and units for a course."""
    from content.models import Module, Unit

    seen_module_paths = set()

    for entry in sorted(os.scandir(course_dir), key=lambda e: e.name):
        if not entry.is_dir() or entry.name.startswith('.') or entry.name == 'images':
            continue

        module_yaml_path = os.path.join(entry.path, 'module.yaml')
        if not os.path.exists(module_yaml_path):
            continue

        try:
            module_data = _parse_yaml_file(module_yaml_path)
            rel_path = os.path.relpath(entry.path, repo_dir)

            # Edge Case 7: Frontmatter validation
            _validate_frontmatter(module_data, 'module', rel_path)

            seen_module_paths.add(rel_path)

            # Derive sort_order and slug from directory name
            sort_order = module_data.get(
                'sort_order', extract_sort_order(entry.name),
            )
            slug = module_data.get('slug', derive_slug(entry.name))

            module, created = Module.objects.update_or_create(
                course=course,
                source_path=rel_path,
                defaults={
                    'title': module_data.get('title', entry.name),
                    'slug': slug,
                    'sort_order': sort_order,
                    'source_repo': repo_name,
                    'source_commit': commit_sha,
                },
            )
            if created:
                stats['created'] += 1
            else:
                stats['updated'] += 1

            # Sync units within this module
            _sync_module_units(
                module, entry.path, repo_dir, repo_name, commit_sha, stats,
                known_images=known_images,
            )

        except Exception as e:
            stats['errors'].append({
                'file': os.path.relpath(module_yaml_path, repo_dir),
                'error': str(e),
            })

    # Remove stale modules
    stale_modules = Module.objects.filter(
        course=course,
        source_repo=repo_name,
    ).exclude(source_path__in=seen_module_paths)
    deleted_count = stale_modules.count()
    stale_modules.delete()
    stats['deleted'] += deleted_count


def _sync_module_units(module, module_dir, repo_dir, repo_name, commit_sha, stats,
                       known_images=None):
    """Sync units (markdown files) within a module directory."""
    from content.models import Unit, UserCourseProgress

    seen_unit_paths = set()
    # Track newly created units with their hashes for rename detection
    new_unit_hashes = {}

    for filename in sorted(os.listdir(module_dir)):
        if not filename.endswith('.md') or filename.upper() == 'README.MD':
            continue

        filepath = os.path.join(module_dir, filename)
        rel_path = os.path.relpath(filepath, repo_dir)

        try:
            metadata, body = _parse_markdown_file(filepath)

            # Edge Case 7: Frontmatter validation
            _validate_frontmatter(metadata, 'unit', rel_path)

            # Require content_id in frontmatter
            unit_content_id = metadata.get('content_id')
            if not unit_content_id:
                msg = f'Skipping {rel_path}: missing content_id in frontmatter'
                logger.warning(msg)
                stats['errors'].append({'file': rel_path, 'error': msg})
                continue

            seen_unit_paths.add(rel_path)

            # Edge Case 1: Compute content hash for rename detection
            content_hash = _compute_content_hash(body)

            # Rewrite image URLs
            base_dir = os.path.dirname(rel_path)

            # Edge Case 8: Check broken image references
            if known_images is not None:
                _check_broken_image_refs(
                    body, rel_path, repo_name, base_dir,
                    known_images, stats.get('errors', []),
                )

            body = rewrite_image_urls(body, repo_name, base_dir)

            is_homework = metadata.get('is_homework', False)

            # Derive sort_order and slug from filename
            sort_order = metadata.get(
                'sort_order', extract_sort_order(filename),
            )
            slug = metadata.get('slug', derive_slug(filename))

            defaults = {
                'title': metadata.get('title', os.path.splitext(filename)[0]),
                'slug': slug,
                'sort_order': sort_order,
                'video_url': metadata.get('video_url', ''),
                'timestamps': metadata.get('timestamps', []),
                'is_preview': metadata.get('is_preview', False),
                'content_hash': content_hash,
                'source_repo': repo_name,
                'source_commit': commit_sha,
                'content_id': unit_content_id,
            }

            if is_homework:
                defaults['homework'] = body
            else:
                defaults['body'] = body

            unit, created = Unit.objects.update_or_create(
                module=module,
                source_path=rel_path,
                defaults=defaults,
            )
            if created:
                stats['created'] += 1
                new_unit_hashes[content_hash] = unit
            else:
                stats['updated'] += 1

        except Exception as e:
            stats['errors'].append({
                'file': rel_path,
                'error': str(e),
            })

    # Remove stale units, with rename detection (Edge Case 1)
    stale_units = Unit.objects.filter(
        module=module,
        source_repo=repo_name,
    ).exclude(source_path__in=seen_unit_paths)

    for stale_unit in stale_units:
        # Check if a newly created unit in the same course has the same hash
        if (stale_unit.content_hash
                and stale_unit.content_hash in new_unit_hashes):
            new_unit = new_unit_hashes[stale_unit.content_hash]
            # Migrate UnitCompletion (UserCourseProgress) records
            migrated = UserCourseProgress.objects.filter(
                unit=stale_unit,
            ).update(unit=new_unit)
            if migrated:
                logger.warning(
                    'Unit appears to have been renamed: %s -> %s, '
                    'migrated %d completion records.',
                    stale_unit.source_path, new_unit.source_path, migrated,
                )

    deleted_count = stale_units.count()
    stale_units.delete()
    stats['deleted'] += deleted_count


def _sync_resources(source, repo_dir, commit_sha, sync_log, known_images=None):
    """Sync resources: recordings, curated links, and downloads."""
    from content.models import CuratedLink, Download, Recording

    stats = {'created': 0, 'updated': 0, 'deleted': 0, 'errors': []}

    # Sync recordings
    recordings_dir = os.path.join(repo_dir, 'recordings')
    if os.path.isdir(recordings_dir):
        _sync_recordings(
            source, recordings_dir, repo_dir, commit_sha, stats,
        )

    # Sync curated links
    links_file = os.path.join(repo_dir, 'curated-links', 'links.yaml')
    if os.path.exists(links_file):
        _sync_curated_links(
            source, links_file, repo_dir, commit_sha, stats,
        )

    # Sync downloads
    downloads_dir = os.path.join(repo_dir, 'downloads')
    if os.path.isdir(downloads_dir):
        _sync_downloads(
            source, downloads_dir, repo_dir, commit_sha, stats,
        )

    return stats


def _sync_recordings(source, recordings_dir, repo_dir, commit_sha, stats):
    """Sync recording YAML files."""
    from content.models import Recording
    from datetime import date as date_type

    seen_slugs = set()
    failed_slugs = set()

    for filename in os.listdir(recordings_dir):
        if not filename.endswith('.yaml') and not filename.endswith('.yml'):
            continue

        filepath = os.path.join(recordings_dir, filename)
        rel_path = os.path.relpath(filepath, repo_dir)

        try:
            data = _parse_yaml_file(filepath)
            slug = data.get('slug', os.path.splitext(filename)[0])

            # Edge Case 7: Frontmatter validation
            _validate_frontmatter(data, 'recording', rel_path)

            # Require content_id in frontmatter
            recording_content_id = data.get('content_id')
            if not recording_content_id:
                msg = f'Skipping {rel_path}: missing content_id in frontmatter'
                logger.warning(msg)
                stats['errors'].append({'file': rel_path, 'error': msg})
                continue

            # Edge Case 2: Slug collision across sources
            if _check_slug_collision(Recording, slug, source.repo_name, rel_path):
                stats['errors'].append({
                    'file': rel_path,
                    'error': (
                        f"Slug collision: '{slug}' already exists from a "
                        f"different source. Skipped."
                    ),
                })
                failed_slugs.add(slug)
                continue

            seen_slugs.add(slug)

            defaults = {
                'title': data.get('title', slug),
                'description': data.get('description', ''),
                'youtube_url': data.get('video_url', ''),
                'timestamps': data.get('timestamps', []),
                'materials': data.get('materials', []),
                'tags': data.get('tags', []),
                'required_level': data.get('required_level', 0),
                'published': True,
                'source_repo': source.repo_name,
                'source_path': rel_path,
                'source_commit': commit_sha,
                'content_id': recording_content_id,
            }

            published_at = data.get('published_at')
            if published_at:
                if isinstance(published_at, str):
                    defaults['date'] = date_type.fromisoformat(published_at)
                elif isinstance(published_at, date_type):
                    defaults['date'] = published_at
            else:
                defaults['date'] = timezone.now().date()

            recording, created = Recording.objects.update_or_create(
                slug=slug,
                source_repo=source.repo_name,
                defaults=defaults,
            )
            if created:
                stats['created'] += 1
            else:
                stats['updated'] += 1

        except Exception as e:
            fallback_slug = os.path.splitext(filename)[0]
            try:
                failed_slug = data.get('slug', fallback_slug)
            except Exception:
                failed_slug = fallback_slug
            failed_slugs.add(failed_slug)
            stats['errors'].append({'file': rel_path, 'error': str(e)})

    # Soft-delete stale recordings, excluding failed slugs
    stale = Recording.objects.filter(
        source_repo=source.repo_name,
        published=True,
    ).exclude(slug__in=seen_slugs).exclude(slug__in=failed_slugs)
    deleted_count = stale.count()
    stale.update(published=False)
    stats['deleted'] += deleted_count


def _sync_curated_links(source, links_file, repo_dir, commit_sha, stats):
    """Sync curated links from a single YAML file."""
    from content.models import CuratedLink

    rel_path = os.path.relpath(links_file, repo_dir)
    seen_item_ids = set()

    try:
        with open(links_file, 'r') as f:
            links_data = yaml.safe_load(f) or []

        if not isinstance(links_data, list):
            links_data = [links_data]

        for idx, link in enumerate(links_data):
            try:
                # Edge Case 7: Frontmatter validation
                _validate_frontmatter(link, 'curated_link', f'{rel_path}[{idx}]')

                item_id = link.get('item_id', f'sync-{idx}')
                seen_item_ids.add(item_id)

                defaults = {
                    'title': link.get('title', ''),
                    'description': link.get('description', ''),
                    'url': link.get('url', ''),
                    'category': link.get('category', 'other'),
                    'tags': link.get('tags', []),
                    'sort_order': link.get('sort_order', idx),
                    'required_level': link.get('required_level', 0),
                    'published': True,
                    'source_repo': source.repo_name,
                    'source_path': rel_path,
                    'source_commit': commit_sha,
                }

                obj, created = CuratedLink.objects.update_or_create(
                    item_id=item_id,
                    defaults=defaults,
                )
                if created:
                    stats['created'] += 1
                else:
                    stats['updated'] += 1
            except Exception as e:
                stats['errors'].append({
                    'file': f'{rel_path}[{idx}]',
                    'error': str(e),
                })

    except Exception as e:
        stats['errors'].append({'file': rel_path, 'error': str(e)})

    # Soft-delete stale links from this repo
    stale = CuratedLink.objects.filter(
        source_repo=source.repo_name,
        published=True,
    ).exclude(item_id__in=seen_item_ids)
    deleted_count = stale.count()
    stale.update(published=False)
    stats['deleted'] += deleted_count


def _sync_downloads(source, downloads_dir, repo_dir, commit_sha, stats):
    """Sync download YAML files."""
    from content.models import Download

    seen_slugs = set()
    failed_slugs = set()

    for filename in os.listdir(downloads_dir):
        if not filename.endswith('.yaml') and not filename.endswith('.yml'):
            continue

        filepath = os.path.join(downloads_dir, filename)
        rel_path = os.path.relpath(filepath, repo_dir)

        try:
            data = _parse_yaml_file(filepath)
            slug = data.get('slug', os.path.splitext(filename)[0])

            # Edge Case 7: Frontmatter validation
            _validate_frontmatter(data, 'download', rel_path)

            # Require content_id in frontmatter
            download_content_id = data.get('content_id')
            if not download_content_id:
                msg = f'Skipping {rel_path}: missing content_id in frontmatter'
                logger.warning(msg)
                stats['errors'].append({'file': rel_path, 'error': msg})
                continue

            # Edge Case 2: Slug collision across sources
            if _check_slug_collision(Download, slug, source.repo_name, rel_path):
                stats['errors'].append({
                    'file': rel_path,
                    'error': (
                        f"Slug collision: '{slug}' already exists from a "
                        f"different source. Skipped."
                    ),
                })
                failed_slugs.add(slug)
                continue

            seen_slugs.add(slug)

            defaults = {
                'title': data.get('title', slug),
                'description': data.get('description', ''),
                'file_url': data.get('file_url', ''),
                'file_type': data.get('file_type', 'other'),
                'file_size_bytes': data.get('file_size_bytes', 0),
                'cover_image_url': data.get('cover_image_url', ''),
                'tags': data.get('tags', []),
                'required_level': data.get('required_level', 0),
                'published': True,
                'source_repo': source.repo_name,
                'source_path': rel_path,
                'source_commit': commit_sha,
                'content_id': download_content_id,
            }

            download, created = Download.objects.update_or_create(
                slug=slug,
                source_repo=source.repo_name,
                defaults=defaults,
            )
            if created:
                stats['created'] += 1
            else:
                stats['updated'] += 1

        except Exception as e:
            fallback_slug = os.path.splitext(filename)[0]
            try:
                failed_slug = data.get('slug', fallback_slug)
            except Exception:
                failed_slug = fallback_slug
            failed_slugs.add(failed_slug)
            stats['errors'].append({'file': rel_path, 'error': str(e)})

    # Soft-delete stale downloads, excluding failed slugs
    stale = Download.objects.filter(
        source_repo=source.repo_name,
        published=True,
    ).exclude(slug__in=seen_slugs).exclude(slug__in=failed_slugs)
    deleted_count = stale.count()
    stale.update(published=False)
    stats['deleted'] += deleted_count


def _sync_projects(source, repo_dir, commit_sha, sync_log, known_images=None):
    """Sync project markdown files from the repo."""
    from content.models import Project
    from datetime import date as date_type

    stats = {'created': 0, 'updated': 0, 'deleted': 0, 'errors': []}
    seen_slugs = set()
    failed_slugs = set()

    for root, dirs, files in os.walk(repo_dir):
        if '.git' in root:
            continue
        for filename in files:
            if not filename.endswith('.md') or filename.upper() == 'README.MD':
                continue

            filepath = os.path.join(root, filename)
            rel_path = os.path.relpath(filepath, repo_dir)

            try:
                metadata, body = _parse_markdown_file(filepath)
                slug = metadata.get('slug', os.path.splitext(filename)[0])

                # Edge Case 7: Frontmatter validation
                _validate_frontmatter(metadata, 'project', rel_path)

                # Require content_id in frontmatter
                project_content_id = metadata.get('content_id')
                if not project_content_id:
                    msg = f'Skipping {rel_path}: missing content_id in frontmatter'
                    logger.warning(msg)
                    stats['errors'].append({'file': rel_path, 'error': msg})
                    continue

                # Edge Case 2: Slug collision across sources
                if _check_slug_collision(Project, slug, source.repo_name, rel_path):
                    stats['errors'].append({
                        'file': rel_path,
                        'error': (
                            f"Slug collision: '{slug}' already exists from a "
                            f"different source. Skipped."
                        ),
                    })
                    failed_slugs.add(slug)
                    continue

                seen_slugs.add(slug)

                base_dir = os.path.dirname(rel_path)

                # Edge Case 8: Check broken image references
                if known_images is not None:
                    _check_broken_image_refs(
                        body, rel_path, source.repo_name, base_dir,
                        known_images, stats['errors'],
                    )

                body = rewrite_image_urls(body, source.repo_name, base_dir)

                defaults = {
                    'title': metadata.get('title', slug),
                    'description': metadata.get('description', ''),
                    'content_markdown': body,
                    'author': metadata.get('author', ''),
                    'tags': metadata.get('tags', []),
                    'difficulty': metadata.get('difficulty', ''),
                    'source_code_url': metadata.get('source_code_url', ''),
                    'demo_url': metadata.get('demo_url', ''),
                    'cover_image_url': metadata.get('cover_image', ''),
                    'required_level': metadata.get('required_level', 0),
                    'published': True,
                    'source_repo': source.repo_name,
                    'source_path': rel_path,
                    'source_commit': commit_sha,
                    'content_id': project_content_id,
                }

                date_val = metadata.get('date')
                if date_val:
                    if isinstance(date_val, str):
                        defaults['date'] = date_type.fromisoformat(date_val)
                    elif isinstance(date_val, date_type):
                        defaults['date'] = date_val
                else:
                    defaults['date'] = timezone.now().date()

                project, created = Project.objects.update_or_create(
                    slug=slug,
                    source_repo=source.repo_name,
                    defaults=defaults,
                )
                if created:
                    stats['created'] += 1
                else:
                    stats['updated'] += 1

            except Exception as e:
                fallback_slug = os.path.splitext(filename)[0]
                try:
                    failed_slug = metadata.get('slug', fallback_slug)
                except Exception:
                    failed_slug = fallback_slug
                failed_slugs.add(failed_slug)
                stats['errors'].append({'file': rel_path, 'error': str(e)})
                logger.warning('Error syncing project %s: %s', rel_path, e)

    # Soft-delete stale projects, excluding failed slugs
    stale = Project.objects.filter(
        source_repo=source.repo_name,
        published=True,
    ).exclude(slug__in=seen_slugs).exclude(slug__in=failed_slugs)
    deleted_count = stale.count()
    stale.update(published=False, status='pending_review')
    stats['deleted'] = deleted_count

    return stats


def _sync_interview_questions(source, repo_dir, commit_sha, sync_log, known_images=None):
    """Sync interview question categories from markdown files."""
    from content.models import InterviewCategory

    stats = {'created': 0, 'updated': 0, 'deleted': 0, 'errors': []}
    seen_slugs = set()

    # Walk for all .md files (excluding README)
    for root, dirs, files in os.walk(repo_dir):
        if '.git' in root:
            continue
        for filename in files:
            if not filename.endswith('.md') or filename.upper() == 'README.MD':
                continue

            filepath = os.path.join(root, filename)
            rel_path = os.path.relpath(filepath, repo_dir)

            try:
                metadata, body = _parse_markdown_file(filepath)
                slug = os.path.splitext(filename)[0]
                seen_slugs.add(slug)

                defaults = {
                    'title': metadata.get('title', slug.replace('-', ' ').title()),
                    'description': metadata.get('description', ''),
                    'status': metadata.get('status', ''),
                    'sections_json': metadata.get('sections', []),
                    'body_markdown': body,
                    'source_repo': source.repo_name,
                    'source_path': rel_path,
                    'source_commit': commit_sha,
                }

                obj, created = InterviewCategory.objects.update_or_create(
                    slug=slug,
                    defaults=defaults,
                )
                if created:
                    stats['created'] += 1
                else:
                    stats['updated'] += 1

            except Exception as e:
                stats['errors'].append({'file': rel_path, 'error': str(e)})
                logger.warning(
                    'Error syncing interview question %s: %s', rel_path, e,
                )

    # Delete stale categories from this repo
    stale = InterviewCategory.objects.filter(
        source_repo=source.repo_name,
    ).exclude(slug__in=seen_slugs)
    deleted_count = stale.count()
    stale.delete()
    stats['deleted'] = deleted_count

    return stats


