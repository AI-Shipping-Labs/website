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
import os
import re
import shutil
import subprocess
import tempfile
import time

import frontmatter
import jwt
import requests
import yaml
from django.conf import settings
from django.utils import timezone

from integrations.models import ContentSource, SyncLog

logger = logging.getLogger(__name__)

GITHUB_API_BASE = 'https://api.github.com'


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
    """Find a ContentSource by repo name.

    Args:
        repo_full_name: Full repo name (e.g. "AI-Shipping-Labs/blog").

    Returns:
        ContentSource or None.
    """
    try:
        return ContentSource.objects.get(repo_name=repo_full_name)
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

    try:
        if os.path.exists(os.path.join(target_dir, '.git')):
            # Pull existing repo
            result = subprocess.run(
                ['git', 'pull', '--ff-only'],
                cwd=target_dir,
                capture_output=True,
                text=True,
                timeout=120,
            )
        else:
            # Clone fresh
            result = subprocess.run(
                ['git', 'clone', '--depth', '1', repo_url, target_dir],
                capture_output=True,
                text=True,
                timeout=120,
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
    """Rewrite relative image URLs in markdown to absolute storage URLs.

    Args:
        markdown_text: Markdown content with relative image paths.
        repo_name: Repo name for the storage path prefix.
        base_path: Base path within the repo for resolving relative paths.

    Returns:
        str: Markdown with rewritten image URLs.
    """
    cdn_base = getattr(settings, 'CONTENT_CDN_BASE', '/static/content-images')
    repo_short = repo_name.split('/')[-1] if '/' in repo_name else repo_name

    def replace_image(match):
        alt = match.group(1)
        path = match.group(2)
        # Skip absolute URLs
        if path.startswith(('http://', 'https://', '/')):
            return match.group(0)
        # Normalize the path
        full_path = os.path.normpath(os.path.join(base_path, path))
        new_url = f'{cdn_base}/{repo_short}/{full_path}'
        return f'![{alt}]({new_url})'

    # Match markdown image syntax: ![alt](path)
    pattern = r'!\[([^\]]*)\]\(([^)]+)\)'
    return re.sub(pattern, replace_image, markdown_text)


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

        # Dispatch to content-type-specific sync
        sync_func = _get_sync_function(source.content_type)
        stats = sync_func(source, repo_dir, commit_sha, sync_log)

        # Update sync log
        sync_log.items_created = stats.get('created', 0)
        sync_log.items_updated = stats.get('updated', 0)
        sync_log.items_deleted = stats.get('deleted', 0)
        sync_log.errors = stats.get('errors', [])

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

    return sync_log


def _get_sync_function(content_type):
    """Return the sync function for a given content type."""
    sync_functions = {
        'article': _sync_articles,
        'course': _sync_courses,
        'resource': _sync_resources,
        'project': _sync_projects,
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


def _sync_articles(source, repo_dir, commit_sha, sync_log):
    """Sync blog articles from the repo."""
    from content.models import Article

    stats = {'created': 0, 'updated': 0, 'deleted': 0, 'errors': []}
    seen_slugs = set()

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

            try:
                metadata, body = _parse_markdown_file(filepath)
                slug = metadata.get('slug', os.path.splitext(filename)[0])
                seen_slugs.add(slug)

                # Rewrite image URLs
                base_dir = os.path.dirname(rel_path)
                body = rewrite_image_urls(body, source.repo_name, base_dir)

                defaults = {
                    'title': metadata.get('title', slug),
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

                article, created = Article.objects.update_or_create(
                    slug=slug,
                    defaults=defaults,
                )
                if created:
                    stats['created'] += 1
                else:
                    stats['updated'] += 1

            except Exception as e:
                stats['errors'].append({
                    'file': rel_path,
                    'error': str(e),
                })
                logger.warning('Error syncing article %s: %s', rel_path, e)

    # Soft-delete articles from this repo that are no longer in the repo
    stale_articles = Article.objects.filter(
        source_repo=source.repo_name,
        published=True,
    ).exclude(slug__in=seen_slugs)

    deleted_count = stale_articles.count()
    stale_articles.update(published=False, status='draft')
    stats['deleted'] = deleted_count

    return stats


def _sync_courses(source, repo_dir, commit_sha, sync_log):
    """Sync courses with modules and units from the repo."""
    from content.models import Course, Module, Unit

    stats = {'created': 0, 'updated': 0, 'deleted': 0, 'errors': []}
    seen_course_slugs = set()

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
            seen_course_slugs.add(slug)
            rel_path = os.path.relpath(entry.path, repo_dir)

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
                'status': 'published',
                'source_repo': source.repo_name,
                'source_path': rel_path,
                'source_commit': commit_sha,
            }

            course, created = Course.objects.update_or_create(
                slug=slug,
                defaults=course_defaults,
            )
            if created:
                stats['created'] += 1
            else:
                stats['updated'] += 1

            # Sync modules
            _sync_course_modules(
                course, entry.path, repo_dir, source.repo_name,
                commit_sha, stats,
            )

        except Exception as e:
            stats['errors'].append({
                'file': os.path.relpath(course_yaml_path, repo_dir),
                'error': str(e),
            })
            logger.warning('Error syncing course %s: %s', entry.name, e)

    # Soft-delete courses from this repo no longer in the repo
    stale_courses = Course.objects.filter(
        source_repo=source.repo_name,
        status='published',
    ).exclude(slug__in=seen_course_slugs)

    deleted_count = stale_courses.count()
    stale_courses.update(status='draft')
    stats['deleted'] += deleted_count

    return stats


def _sync_course_modules(course, course_dir, repo_dir, repo_name, commit_sha, stats):
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
            seen_module_paths.add(rel_path)

            module, created = Module.objects.update_or_create(
                course=course,
                source_path=rel_path,
                defaults={
                    'title': module_data.get('title', entry.name),
                    'sort_order': module_data.get('sort_order', 0),
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


def _sync_module_units(module, module_dir, repo_dir, repo_name, commit_sha, stats):
    """Sync units (markdown files) within a module directory."""
    from content.models import Unit

    seen_unit_paths = set()

    for filename in sorted(os.listdir(module_dir)):
        if not filename.endswith('.md') or filename.upper() == 'README.MD':
            continue

        filepath = os.path.join(module_dir, filename)
        rel_path = os.path.relpath(filepath, repo_dir)

        try:
            metadata, body = _parse_markdown_file(filepath)
            seen_unit_paths.add(rel_path)

            # Rewrite image URLs
            base_dir = os.path.dirname(rel_path)
            body = rewrite_image_urls(body, repo_name, base_dir)

            is_homework = metadata.get('is_homework', False)

            defaults = {
                'title': metadata.get('title', os.path.splitext(filename)[0]),
                'sort_order': metadata.get('sort_order', 0),
                'video_url': metadata.get('video_url', ''),
                'timestamps': metadata.get('timestamps', []),
                'is_preview': metadata.get('is_preview', False),
                'source_repo': repo_name,
                'source_commit': commit_sha,
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
            else:
                stats['updated'] += 1

        except Exception as e:
            stats['errors'].append({
                'file': rel_path,
                'error': str(e),
            })

    # Remove stale units
    stale_units = Unit.objects.filter(
        module=module,
        source_repo=repo_name,
    ).exclude(source_path__in=seen_unit_paths)
    deleted_count = stale_units.count()
    stale_units.delete()
    stats['deleted'] += deleted_count


def _sync_resources(source, repo_dir, commit_sha, sync_log):
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

    for filename in os.listdir(recordings_dir):
        if not filename.endswith('.yaml') and not filename.endswith('.yml'):
            continue

        filepath = os.path.join(recordings_dir, filename)
        rel_path = os.path.relpath(filepath, repo_dir)

        try:
            data = _parse_yaml_file(filepath)
            slug = data.get('slug', os.path.splitext(filename)[0])
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
                defaults=defaults,
            )
            if created:
                stats['created'] += 1
            else:
                stats['updated'] += 1

        except Exception as e:
            stats['errors'].append({'file': rel_path, 'error': str(e)})

    # Soft-delete stale recordings from this repo
    stale = Recording.objects.filter(
        source_repo=source.repo_name,
        published=True,
    ).exclude(slug__in=seen_slugs)
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

    for filename in os.listdir(downloads_dir):
        if not filename.endswith('.yaml') and not filename.endswith('.yml'):
            continue

        filepath = os.path.join(downloads_dir, filename)
        rel_path = os.path.relpath(filepath, repo_dir)

        try:
            data = _parse_yaml_file(filepath)
            slug = data.get('slug', os.path.splitext(filename)[0])
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
            }

            download, created = Download.objects.update_or_create(
                slug=slug,
                defaults=defaults,
            )
            if created:
                stats['created'] += 1
            else:
                stats['updated'] += 1

        except Exception as e:
            stats['errors'].append({'file': rel_path, 'error': str(e)})

    # Soft-delete stale downloads
    stale = Download.objects.filter(
        source_repo=source.repo_name,
        published=True,
    ).exclude(slug__in=seen_slugs)
    deleted_count = stale.count()
    stale.update(published=False)
    stats['deleted'] += deleted_count


def _sync_projects(source, repo_dir, commit_sha, sync_log):
    """Sync project markdown files from the repo."""
    from content.models import Project
    from datetime import date as date_type

    stats = {'created': 0, 'updated': 0, 'deleted': 0, 'errors': []}
    seen_slugs = set()

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
                seen_slugs.add(slug)

                base_dir = os.path.dirname(rel_path)
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
                    defaults=defaults,
                )
                if created:
                    stats['created'] += 1
                else:
                    stats['updated'] += 1

            except Exception as e:
                stats['errors'].append({'file': rel_path, 'error': str(e)})
                logger.warning('Error syncing project %s: %s', rel_path, e)

    # Soft-delete stale projects
    stale = Project.objects.filter(
        source_repo=source.repo_name,
        published=True,
    ).exclude(slug__in=seen_slugs)
    deleted_count = stale.count()
    stale.update(published=False, status='pending_review')
    stats['deleted'] = deleted_count

    return stats
