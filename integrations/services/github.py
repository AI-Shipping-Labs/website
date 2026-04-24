"""GitHub integration service for content sync.

Handles:
- Webhook signature validation (X-Hub-Signature-256)
- GitHub App authentication for private repos
- Repository cloning/pulling
- Content sync: parse markdown/YAML, upload images, upsert content
"""

import base64
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
import uuid
from pathlib import PurePath

import boto3
import frontmatter
import jwt
import requests
import yaml
from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError
from django.utils import timezone

from integrations.config import get_config
from integrations.models import ContentSource, SyncLog

logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.ico'}
CONTENT_EXTENSIONS = {'.md', '.yaml', '.yml'}

GITHUB_API_BASE = 'https://api.github.com'

# Cache key + TTL for the repositories accessible to the GitHub App installation.
INSTALLATION_REPOS_CACHE_KEY = 'github_installation_repositories'
INSTALLATION_REPOS_CACHE_TIMEOUT = 60  # seconds

# Cache key prefix + TTL for content-type detection results per repo.
DETECT_CONTENT_CACHE_KEY_PREFIX = 'github_detect_content:'
DETECT_CONTENT_CACHE_TIMEOUT = 60  # seconds

# Hard upper bound on the number of files we will fetch frontmatter from when
# auto-detecting content types for a repo. Prevents runaway API usage on huge
# repos (the recursive tree listing already gives us file paths cheaply).
DETECT_FRONTMATTER_FETCH_LIMIT = 200

# Required frontmatter fields per content type
REQUIRED_FIELDS = {
    'article': ['title'],
    'course': ['title'],
    'module': ['title'],
    'unit': ['title'],
    'event': ['title'],
    'project': ['title'],
    'curated_link': ['title', 'url', 'item_id'],
    'download': ['title'],
    'workshop': ['content_id', 'slug', 'title', 'pages_required_level'],
    'workshop_page': ['title'],
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


def _matches_ignore_patterns(rel_path, patterns):
    """Return True if ``rel_path`` matches any glob in ``patterns``.

    Uses :meth:`pathlib.PurePath.full_match` (Python 3.13+) so recursive
    ``**`` globs work as expected. ``rel_path`` must be relative to whichever
    directory the ignore patterns were declared against (course root for
    course-level ``ignore:``, module dir for module-level).
    """
    if not patterns:
        return False
    p = PurePath(rel_path)
    for pattern in patterns:
        if not pattern:
            continue
        try:
            if p.full_match(pattern):
                return True
        except (ValueError, TypeError):
            # Malformed glob – treat as non-matching rather than blowing up sync.
            continue
    return False


def _extract_readme_title(body, fallback):
    """Return the first Markdown H1 heading in ``body`` or ``fallback``."""
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith('# ') and not stripped.startswith('## '):
            return stripped[2:].strip() or fallback
    return fallback


def _derive_readme_content_id(repo_name, module_source_path):
    """Derive a stable UUIDv5 content_id for a module's README-as-unit.

    Used when the README has no explicit ``content_id`` in frontmatter. The
    namespace key combines the repo name and module source path so the UUID is
    stable across syncs and unique across modules/repos.
    """
    key = f'{repo_name}:{module_source_path}:readme'
    return str(uuid.uuid5(uuid.NAMESPACE_URL, key))


def _derive_workshop_page_content_id(repo_name, page_source_path):
    """Derive a stable UUIDv5 content_id for a workshop page.

    Workshop pages are markdown files under ``YYYY/<date-slug>/*.md``. Authors
    rarely want to hand-write a UUID for every page, so the sync derives a
    stable one from ``(repo_name, source_path)``. Mirror
    :func:`_derive_readme_content_id` but namespaced for workshop pages.
    """
    key = f'{repo_name}:{page_source_path}:workshop_page'
    return str(uuid.uuid5(uuid.NAMESPACE_URL, key))


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
    app_id = get_config('GITHUB_APP_ID')
    private_key = get_config('GITHUB_APP_PRIVATE_KEY')
    installation_id = get_config('GITHUB_APP_INSTALLATION_ID')

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


def list_installation_repositories(force_refresh=False):
    """List repositories accessible to the GitHub App installation.

    Calls ``GET /installation/repositories`` using a freshly minted installation
    token. Pages through results so all accessible repos are returned. The
    response is cached briefly (``INSTALLATION_REPOS_CACHE_TIMEOUT`` seconds)
    to avoid hammering the GitHub API when the Studio form is reopened.

    Args:
        force_refresh: If True, bypass the cache and re-fetch from GitHub.

    Returns:
        list[dict]: One entry per repo with keys
            ``full_name`` (e.g. ``"AI-Shipping-Labs/content"``),
            ``private`` (bool),
            ``default_branch`` (str).
        Sorted alphabetically by ``full_name`` (case-insensitive).

    Raises:
        GitHubSyncError: If credentials are missing or the API call fails.
    """
    if not force_refresh:
        cached = cache.get(INSTALLATION_REPOS_CACHE_KEY)
        if cached is not None:
            return cached

    token = generate_github_app_token()
    headers = {
        'Authorization': f'token {token}',
        'Accept': 'application/vnd.github+json',
    }

    repos = []
    page = 1
    per_page = 100
    # Hard upper bound on pages to avoid runaway loops on a misbehaving API.
    max_pages = 20
    while page <= max_pages:
        response = requests.get(
            f'{GITHUB_API_BASE}/installation/repositories',
            headers=headers,
            params={'per_page': per_page, 'page': page},
            timeout=15,
        )
        if response.status_code != 200:
            raise GitHubSyncError(
                f'Failed to list installation repositories: '
                f'{response.status_code} {response.text}'
            )

        payload = response.json()
        page_repos = payload.get('repositories', []) or []
        for repo in page_repos:
            repos.append({
                'full_name': repo.get('full_name', ''),
                'private': bool(repo.get('private', False)),
                'default_branch': repo.get('default_branch', '') or 'main',
            })

        if len(page_repos) < per_page:
            break
        page += 1

    repos.sort(key=lambda r: r['full_name'].lower())

    cache.set(INSTALLATION_REPOS_CACHE_KEY, repos, INSTALLATION_REPOS_CACHE_TIMEOUT)
    return repos


def clear_installation_repositories_cache():
    """Drop the cached installation repository list so the next call re-fetches."""
    cache.delete(INSTALLATION_REPOS_CACHE_KEY)


def _get_repo_metadata(repo_full_name, token):
    """Return the repository metadata dict from ``GET /repos/{owner}/{repo}``.

    Used by detection to discover the default branch (the tree listing requires
    a branch ref).
    """
    response = requests.get(
        f'{GITHUB_API_BASE}/repos/{repo_full_name}',
        headers={
            'Authorization': f'token {token}',
            'Accept': 'application/vnd.github+json',
        },
        timeout=15,
    )
    if response.status_code != 200:
        raise GitHubSyncError(
            f'Failed to fetch repo metadata for {repo_full_name}: '
            f'{response.status_code} {response.text}'
        )
    return response.json()


def _list_repo_tree(repo_full_name, branch, token):
    """List all paths in the repo via the recursive Git Trees API.

    Returns a list of dicts: ``{'path': str, 'type': 'blob'|'tree'}``.

    Uses the recursive tree listing so we get the entire repo structure in a
    single request (no per-directory walking, no clone). The trees API caps
    at ~100k entries with a ``truncated`` flag; for our repos this is fine.
    """
    response = requests.get(
        f'{GITHUB_API_BASE}/repos/{repo_full_name}/git/trees/{branch}',
        headers={
            'Authorization': f'token {token}',
            'Accept': 'application/vnd.github+json',
        },
        params={'recursive': '1'},
        timeout=15,
    )
    if response.status_code != 200:
        raise GitHubSyncError(
            f'Failed to list tree for {repo_full_name}@{branch}: '
            f'{response.status_code} {response.text}'
        )
    payload = response.json() or {}
    return payload.get('tree', []) or []


def _fetch_repo_file(repo_full_name, path, branch, token):
    """Fetch a single file's text contents via the GitHub Contents API.

    Returns the decoded UTF-8 string, or ``None`` if the file is binary,
    too large, or unreadable.
    """
    response = requests.get(
        f'{GITHUB_API_BASE}/repos/{repo_full_name}/contents/{path}',
        headers={
            'Authorization': f'token {token}',
            'Accept': 'application/vnd.github+json',
        },
        params={'ref': branch},
        timeout=15,
    )
    if response.status_code != 200:
        return None
    payload = response.json() or {}
    if payload.get('encoding') != 'base64':
        return None
    raw = payload.get('content') or ''
    try:
        return base64.b64decode(raw).decode('utf-8')
    except (ValueError, UnicodeDecodeError):
        return None


def _parse_frontmatter_text(text):
    """Parse YAML frontmatter from a markdown text string.

    Returns the metadata dict, or an empty dict if no frontmatter is present
    or it fails to parse.
    """
    if not text:
        return {}
    try:
        post = frontmatter.loads(text)
        return dict(post.metadata or {})
    except Exception:
        return {}


def _parse_yaml_text(text):
    """Parse a YAML document from a string. Returns ``{}`` on failure."""
    if not text:
        return {}
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def _interview_question_filename(name):
    """Return True if a root-level filename looks like an interview question.

    Convention: ``<topic>.md`` at the repo root, lowercase kebab-case, not
    a README. Examples: ``python.md``, ``machine-learning.md``.
    """
    if not name.endswith('.md'):
        return False
    base = name[:-3]
    if not base:
        return False
    if name.upper() == 'README.MD':
        return False
    # Allow lowercase letters, digits, and dashes only.
    return all(c.islower() or c.isdigit() or c == '-' for c in base)


def detect_content_sources(repo_full_name, *, force_refresh=False):
    """Auto-detect ``ContentSource`` rows we should create for ``repo_full_name``.

    Walks the repo via the recursive Git Trees API and inspects a small sample
    of files via the Contents API to look for the structural signals listed in
    issue #213. Result is a list of ``{content_type, content_path, summary}``
    dicts; an empty list means "nothing recognized" and the caller should show
    an actionable error.

    Detection rules (priority order):

    - ``course.yaml`` at the root  -> ``course`` at ``''``
    - ``<dir>/course.yaml``        -> ``course`` at ``<dir>``
    - YAML with ``start_datetime`` -> ``event`` at the file's directory
    - Markdown with ``difficulty`` AND ``author`` frontmatter
                                    -> ``project`` at the file's directory
    - Markdown with ``date:``      -> ``article`` at the file's directory
    - ``<root>/<topic>.md`` (lowercase kebab-case, not README, not consumed
      above) -> ``interview_question`` at ``''``

    Each detected ``(content_type, content_path)`` pair is reported once even
    if many files match, so the monorepo case yields one row per directory.
    Results are cached for ``DETECT_CONTENT_CACHE_TIMEOUT`` seconds.
    """
    cache_key = f'{DETECT_CONTENT_CACHE_KEY_PREFIX}{repo_full_name}'
    if not force_refresh:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

    token = generate_github_app_token()
    metadata = _get_repo_metadata(repo_full_name, token)
    branch = metadata.get('default_branch') or 'main'

    tree = _list_repo_tree(repo_full_name, branch, token)

    # Bucket file paths by directory and extension for cheap iteration.
    yaml_paths = []
    md_paths = []
    course_yaml_dirs = set()  # dirs that contain a ``course.yaml`` (or '' for root)
    for entry in tree:
        if entry.get('type') != 'blob':
            continue
        path = entry.get('path') or ''
        if not path:
            continue
        if path.startswith('.git/'):
            continue
        name = path.rsplit('/', 1)[-1]
        if name == 'course.yaml':
            parent = path.rsplit('/', 1)[0] if '/' in path else ''
            course_yaml_dirs.add(parent)
            continue
        if name.lower().endswith(('.yaml', '.yml')):
            yaml_paths.append(path)
        elif name.lower().endswith('.md'):
            md_paths.append(path)

    detections = []
    seen_keys = set()  # (content_type, content_path) pairs already added

    def _add(content_type, content_path, summary):
        key = (content_type, content_path)
        if key in seen_keys:
            return
        seen_keys.add(key)
        detections.append({
            'content_type': content_type,
            'content_path': content_path,
            'summary': summary,
        })

    # Rule 1+2: course.yaml at root or in a single subdirectory.
    # If course.yaml lives at the root, register ``course`` at ``''``.
    # If course.yaml lives in subdirs (e.g. ``courses/foo/course.yaml``), the
    # multi-course layout is handled by registering the parent directory
    # ``courses/``. We collapse all matching sibling-of-course.yaml dirs to
    # their common parent so a monorepo with many courses gets one source.
    if '' in course_yaml_dirs:
        _add('course', '', 'course.yaml found at repo root')
    nested = course_yaml_dirs - {''}
    if nested:
        # Group by the parent of the course.yaml's containing dir.
        # For ``courses/foo/course.yaml`` parent dir is ``courses/foo``,
        # grandparent is ``courses``. We register the grandparent as the
        # content path so the multi-course sync sees ``courses/<slug>/``.
        course_parents = set()
        for d in nested:
            parts = d.split('/')
            if len(parts) >= 1:
                course_parents.add(parts[0])
        for parent in sorted(course_parents):
            _add('course', parent, f'course.yaml found under {parent}/')

    # Track files we've fetched so we don't re-fetch and so we can attribute
    # ownership: a markdown file claimed by ``project`` should not also count
    # as ``article`` even though it likely has a ``date:``.
    fetched = 0
    project_dirs_with_match = set()
    article_dirs_with_match = set()
    event_dirs_with_match = set()

    # Cap how many files we sample. The tree already constrains us; this is
    # belt-and-braces.
    yaml_sample = yaml_paths[:DETECT_FRONTMATTER_FETCH_LIMIT]
    md_sample = md_paths[:DETECT_FRONTMATTER_FETCH_LIMIT]

    # Rule 3: events. YAML files with ``start_datetime``.
    for path in yaml_sample:
        if fetched >= DETECT_FRONTMATTER_FETCH_LIMIT:
            break
        text = _fetch_repo_file(repo_full_name, path, branch, token)
        fetched += 1
        if text is None:
            continue
        data = _parse_yaml_text(text)
        if 'start_datetime' in data:
            parent = path.rsplit('/', 1)[0] if '/' in path else ''
            event_dirs_with_match.add(parent)

    for parent in sorted(event_dirs_with_match):
        label = parent or 'repo root'
        _add('event', parent, f'YAML files with start_datetime found in {label}')

    # Rules 4 + 5: project (difficulty + author) and article (date) markdown.
    for path in md_sample:
        if fetched >= DETECT_FRONTMATTER_FETCH_LIMIT:
            break
        name = path.rsplit('/', 1)[-1]
        if name.upper() == 'README.MD':
            continue
        text = _fetch_repo_file(repo_full_name, path, branch, token)
        fetched += 1
        if text is None:
            continue
        meta = _parse_frontmatter_text(text)
        parent = path.rsplit('/', 1)[0] if '/' in path else ''

        if meta.get('difficulty') and meta.get('author'):
            project_dirs_with_match.add(parent)
            continue  # don't double-claim as article
        if meta.get('date'):
            article_dirs_with_match.add(parent)

    for parent in sorted(project_dirs_with_match):
        label = parent or 'repo root'
        _add('project', parent, f'markdown with difficulty + author found in {label}')

    for parent in sorted(article_dirs_with_match):
        label = parent or 'repo root'
        _add('article', parent, f'markdown with date frontmatter found in {label}')

    # Rule 6: interview-question convention. Lowercase kebab-case ``.md`` files
    # at the repo root that we did not already classify as article/project.
    consumed_root_md = (
        '' in article_dirs_with_match or '' in project_dirs_with_match
    )
    if not consumed_root_md:
        root_md = [
            p for p in md_paths
            if '/' not in p and _interview_question_filename(p)
        ]
        if root_md:
            _add(
                'interview_question', '',
                f'{len(root_md)} root-level markdown files matching '
                'interview-question convention',
            )

    cache.set(cache_key, detections, DETECT_CONTENT_CACHE_TIMEOUT)
    return detections


def clear_detect_content_sources_cache(repo_full_name=None):
    """Drop the cached detection results.

    With no argument, drops every cached detection. With ``repo_full_name``,
    drops just that one entry (used by the "Refresh repo list" button).
    """
    if repo_full_name is None:
        # No public way to enumerate keys with the local-memory cache; clear
        # the whole cache as a safe fallback.
        cache.clear()
        return
    cache.delete(f'{DETECT_CONTENT_CACHE_KEY_PREFIX}{repo_full_name}')


def _resolve_local_repo_sha(repo_dir):
    """Return ``git rev-parse HEAD`` for a local checkout, or ``''``.

    Used by ``sync_content_source`` when ``repo_dir`` is provided
    (``--from-disk`` syncs and tests). Falls back silently when the
    directory isn't a git checkout — most test fixtures aren't.
    """
    if not os.path.isdir(os.path.join(repo_dir, '.git')):
        return ''
    try:
        result = subprocess.run(
            ['git', 'rev-parse', 'HEAD'],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ''
    if result.returncode != 0:
        return ''
    sha = result.stdout.strip()
    return sha if re.fullmatch(r'[0-9a-f]{40}', sha) else ''


def fetch_remote_head_sha(repo_name, is_private=False, timeout=15):
    """Cheaply fetch the upstream HEAD commit SHA for a repo.

    Uses ``git ls-remote <url> HEAD`` which is much cheaper than a full
    clone — no working tree, no object pack download. Used by
    :func:`sync_content_source` to short-circuit no-op syncs when the
    upstream HEAD has not changed since the last successful sync
    (issue #235).

    Args:
        repo_name: Full repo name (e.g. ``"AI-Shipping-Labs/blog"``).
        is_private: Whether the repo requires authentication.
        timeout: Subprocess timeout in seconds.

    Returns:
        str | None: The HEAD commit SHA, or ``None`` if the lookup failed.
            Failures are intentionally non-fatal so the caller can fall back
            to running the sync — we never silently skip when we can't
            verify HEAD.
    """
    if is_private:
        try:
            token = generate_github_app_token()
        except GitHubSyncError as e:
            logger.warning(
                'Could not get GitHub App token for HEAD check on %s: %s',
                repo_name, e,
            )
            return None
        repo_url = f'https://x-access-token:{token}@github.com/{repo_name}.git'
    else:
        repo_url = f'https://github.com/{repo_name}.git'

    try:
        result = subprocess.run(
            ['git', 'ls-remote', repo_url, 'HEAD'],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        logger.warning('git ls-remote failed for %s: %s', repo_name, e)
        return None

    if result.returncode != 0:
        logger.warning(
            'git ls-remote returned %s for %s: %s',
            result.returncode, repo_name, result.stderr.strip(),
        )
        return None

    # Output is "<sha>\tHEAD\n"
    line = result.stdout.strip().split('\n', 1)[0]
    sha = line.split('\t', 1)[0].strip()
    if not re.fullmatch(r'[0-9a-f]{40}', sha):
        logger.warning('Unexpected ls-remote output for %s: %r', repo_name, line)
        return None
    return sha


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
    cdn_base = get_config('CONTENT_CDN_BASE', '/static/content-images')
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

    cdn_base = get_config('CONTENT_CDN_BASE', '/static/content-images')
    repo_short = source.repo_name.split('/')[-1] if '/' in source.repo_name else source.repo_name
    clean_path = cover_image.lstrip('/')
    base_dir = os.path.dirname(rel_path)
    full_path = os.path.normpath(os.path.join(base_dir, clean_path))
    return f'{cdn_base}/{repo_short}/{full_path}'


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


def sync_content_source(source, repo_dir=None, batch_id=None, force=False):
    """Sync content from a GitHub repo into the database.

    This is the main sync function that:
    1. Clones/pulls the repo
    2. Parses all content files
    3. Upserts content into the database
    4. Soft-deletes content no longer in the repo
    5. Logs the result

    Args:
        source: ContentSource instance.
        repo_dir: Optional pre-cloned repo directory (for testing /
            ``--from-disk`` syncs). When provided, the cheap HEAD-SHA
            skip optimisation (issue #235) is bypassed because there's
            no remote to compare against.
        batch_id: Optional UUID to group logs from the same "Sync All" action.
        force: If True, bypass the HEAD-SHA skip check and force a full
            sync even when the upstream HEAD hasn't changed (issue #235).
            Used by the Studio "Force resync" button and the webhook
            handler (which already knows a new commit landed).

    Returns:
        SyncLog or None: The sync log entry.
    """
    # Acquire sync lock (Edge Case 4: Concurrent Syncs)
    # Skip locking when repo_dir is provided (testing mode)
    use_lock = repo_dir is None
    if use_lock and not acquire_sync_lock(source):
        logger.info(
            'Sync already in progress for %s, skipping.', source.repo_name,
        )
        # Issue #221: the in-memory ``source`` may point to a row that was
        # deleted (or rolled back under SQLite contention) before this task
        # ran. Writing the SyncLog with a stale FK raises IntegrityError and
        # fails the worker task. Confirm the source still exists, and treat
        # the SyncLog write itself as best-effort.
        if not ContentSource.objects.filter(pk=source.pk).exists():
            logger.warning(
                'Skipping SyncLog for %s: ContentSource %s no longer exists.',
                source.repo_name, source.pk,
            )
            return None
        try:
            sync_log = SyncLog.objects.create(
                source=source,
                batch_id=batch_id,
                status='skipped',
                finished_at=timezone.now(),
                errors=[
                    {'file': '', 'error': 'Sync already in progress, skipped.'},
                ],
            )
        except IntegrityError:
            logger.warning(
                'Could not write skipped SyncLog for %s (FK gone); '
                'returning without raising.',
                source.repo_name,
            )
            return None
        return sync_log

    # Cheap HEAD-SHA skip check (issue #235).
    #
    # If the upstream HEAD hasn't moved since the last successful sync, a
    # full clone + file walk would be a no-op. Use ``git ls-remote HEAD``
    # (a single network round-trip, no object download) to compare. Skip
    # the optimisation when:
    #   - ``force=True`` (operator clicked "Force resync" or webhook fired)
    #   - ``repo_dir`` is provided (no remote to check; tests + ``--from-disk``)
    #   - the previous sync didn't end in ``success`` (we want to retry)
    #   - we don't yet have a baseline (first-ever sync)
    #
    # If the HEAD lookup itself fails (network error, missing auth) we
    # fall through and run the sync rather than silently skipping —
    # better to do the work than to lie about being up to date.
    if (
        repo_dir is None
        and not force
        and source.last_synced_commit
        and source.last_sync_status == 'success'
    ):
        head_sha = fetch_remote_head_sha(source.repo_name, source.is_private)
        if head_sha and head_sha == source.last_synced_commit:
            now = timezone.now()
            sync_log = SyncLog.objects.create(
                source=source,
                batch_id=batch_id,
                status='skipped',
                commit_sha=head_sha,
                finished_at=now,
                errors=[{'file': '', 'error': 'HEAD unchanged'}],
            )
            source.last_synced_at = now
            source.last_sync_status = 'skipped'
            source.last_sync_log = f'Skipped: HEAD unchanged ({head_sha[:7]})'
            source.save(update_fields=[
                'last_synced_at', 'last_sync_status', 'last_sync_log',
                'updated_at',
            ])
            if use_lock:
                release_sync_lock(source)
            logger.info(
                'Skipping sync for %s — HEAD unchanged (%s).',
                source.repo_name, head_sha[:7],
            )
            return sync_log

    # Issue #274: queued → running transition.
    #
    # The Studio trigger views now create a SyncLog at status='queued' the
    # moment the operator clicks "Sync now". When the worker (this code)
    # finally picks up the task, we UPDATE that existing row instead of
    # creating a duplicate — otherwise the dashboard would show two rows
    # for one logical sync (a queued one and a running one).
    #
    # We look for the most recent queued row for this source within the
    # last queued-watchdog window (only those rows could plausibly belong
    # to *this* enqueue call). If none found — direct CLI invocation,
    # webhook bypass, etc. — fall back to creating one as before.
    from django.conf import settings as _settings
    queued_window = timezone.now() - timezone.timedelta(
        minutes=getattr(_settings, 'SYNC_QUEUED_THRESHOLD_MINUTES', 10),
    )
    queued_log = SyncLog.objects.filter(
        source=source,
        status='queued',
        started_at__gte=queued_window,
    ).order_by('-started_at').first()
    if queued_log is not None:
        queued_log.status = 'running'
        # Carry batch_id from the worker call if the trigger view didn't
        # know it (single-source trigger); otherwise the trigger view's
        # batch_id was already written when the queued row was created.
        if batch_id and not queued_log.batch_id:
            queued_log.batch_id = batch_id
            queued_log.save(update_fields=['status', 'batch_id'])
        else:
            queued_log.save(update_fields=['status'])
        sync_log = queued_log
    else:
        sync_log = SyncLog.objects.create(
            source=source,
            batch_id=batch_id,
            status='running',
        )

    source.last_sync_status = 'running'
    source.save(update_fields=['last_sync_status', 'updated_at'])

    temp_dir = None
    commit_sha = ''
    try:
        if repo_dir is None:
            temp_dir = tempfile.mkdtemp(prefix='github-sync-')
            commit_sha = clone_or_pull_repo(
                source.repo_name, temp_dir, source.is_private,
            )
            repo_dir = temp_dir
        else:
            # For testing / --from-disk, resolve the SHA from the local
            # clone if possible; otherwise fall back to the legacy
            # ``test-commit-sha`` marker so downstream per-item
            # ``source_commit`` writes (and tests asserting on it) still
            # have a value.
            commit_sha = _resolve_local_repo_sha(repo_dir) or 'test-commit-sha'

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

        # Sync tiers.yaml from repo root into SiteConfig
        tiers_result = _sync_tiers_yaml(repo_dir)

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
        sync_log.items_unchanged = stats.get('unchanged', 0)
        sync_log.items_deleted = stats.get('deleted', 0)
        sync_log.items_detail = stats.get('items_detail', [])
        sync_log.tiers_synced = tiers_result.get('synced', False)
        sync_log.tiers_count = tiers_result.get('count', 0)
        sync_log.errors = all_errors
        sync_log.commit_sha = commit_sha or ''

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
            f"Unchanged: {sync_log.items_unchanged}, "
            f"Deleted: {sync_log.items_deleted}"
        )
        if sync_log.errors:
            source.last_sync_log += f"\nErrors: {len(sync_log.errors)}"
        # Issue #235: only persist last_synced_commit on success/partial
        # (i.e. the sync at least completed without raising). On failure
        # we keep the previous last-good SHA so the next skip check still
        # has a baseline. We treat ``partial`` (per-file errors) as good
        # enough — the repo was processed, we know what we have.
        if commit_sha and commit_sha != 'test-commit-sha':
            source.last_synced_commit = commit_sha
        source.save()

    except Exception as e:
        logger.exception('Sync failed for %s', source.repo_name)
        sync_log.status = 'failed'
        sync_log.finished_at = timezone.now()
        sync_log.errors = [{'file': '', 'error': str(e)}]
        # Record which SHA we were trying to sync against (helps debug
        # repeated failures), but do NOT update ContentSource —
        # ``last_synced_commit`` is a "last known good" pointer.
        if commit_sha:
            sync_log.commit_sha = commit_sha
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


def _sync_tiers_yaml(repo_dir):
    """Sync tiers.yaml from the repo root into SiteConfig.

    Returns:
        dict with keys ``synced`` (bool) and ``count`` (int), or
        ``synced=False, count=0`` when the file is absent or fails.
    """
    tiers_path = os.path.join(repo_dir, 'tiers.yaml')
    if not os.path.isfile(tiers_path):
        return {'synced': False, 'count': 0}
    try:
        import yaml

        from content.models import SiteConfig
        with open(tiers_path, encoding='utf-8') as f:
            tiers_data = yaml.safe_load(f) or []
        SiteConfig.objects.update_or_create(
            key='tiers',
            defaults={'data': tiers_data},
        )
        logger.info('tiers.yaml synced to SiteConfig (%d tiers)', len(tiers_data))
        return {'synced': True, 'count': len(tiers_data)}
    except Exception as e:
        logger.warning('Failed to sync tiers.yaml: %s', e)
        return {'synced': False, 'count': 0}


def _get_sync_function(content_type):
    """Return the sync function for a given content type."""
    sync_functions = {
        'article': _sync_articles,
        'course': _sync_courses,
        'resource': _sync_resources,
        'project': _sync_projects,
        'interview_question': _sync_interview_questions,
        'event': _sync_events,
        'workshop': _sync_workshops,
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
    post = frontmatter.load(filepath, encoding='utf-8')
    return dict(post.metadata), post.content


def _parse_yaml_file(filepath):
    """Parse a YAML file.

    Args:
        filepath: Path to the YAML file.

    Returns:
        dict: Parsed YAML data.
    """
    with open(filepath, 'r', encoding='utf-8') as f:
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
    # A field is "missing" when the key is absent or its value is None,
    # an empty string, or an empty list. Crucially, ``0`` counts as
    # present — numeric fields like ``pages_required_level`` legitimately
    # accept zero (``LEVEL_OPEN``) and must not trip the "missing" check.
    missing = [
        f for f in required
        if metadata.get(f) is None or metadata.get(f) == '' or metadata.get(f) == []
    ]
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


# Fields ignored when deciding whether a re-sync actually changed an item.
# ``source_commit`` bumps on every sync to whatever the current HEAD is, so
# comparing it would mark every item as updated (defeating the whole point of
# issue #225). ``source_repo`` and ``source_path`` are scope/identity fields
# we look up by; they would always be equal and including them is just noise.
_NO_CHANGE_IGNORED_FIELDS = frozenset({
    'source_commit',
    'source_repo',
    'source_path',
})


def _defaults_differ(instance, defaults):
    """Return True if any value in ``defaults`` differs from ``instance``.

    Used by the per-content-type sync helpers to decide whether an existing
    row needs to be re-saved on a re-sync (issue #225). When this returns
    False the sync skips the save AND skips the items_detail entry, so the
    sync report only lists items whose content actually changed.

    Notes on normalization:

    - ``tags`` is a JSONField list that the model ``save()`` normalizes
      (lowercase, hyphenated). The incoming defaults have not been
      normalized yet, so we normalize before comparing — otherwise an
      author-cased tag like ``Python`` would always look different from
      the stored ``python`` and the row would re-save on every sync.
    - Fields in :data:`_NO_CHANGE_IGNORED_FIELDS` are skipped because they
      either change every run (``source_commit``) or are scope keys that
      cannot differ for a row we just looked up (``source_repo``,
      ``source_path``).
    """
    for field, new_value in defaults.items():
        if field in _NO_CHANGE_IGNORED_FIELDS:
            continue
        current = getattr(instance, field, None)
        if field == 'tags' and isinstance(new_value, list):
            from content.utils.tags import normalize_tags
            new_value = normalize_tags(new_value)
        # ``content_id`` is stored as a UUID but YAML frontmatter parses
        # to a string. Coerce both sides to a UUID for the comparison so a
        # re-sync of the same file doesn't look like a diff.
        if field == 'content_id' and current is not None and new_value is not None:
            if isinstance(current, uuid.UUID) and isinstance(new_value, str):
                try:
                    new_value = uuid.UUID(new_value)
                except (ValueError, AttributeError):
                    pass
            elif isinstance(new_value, uuid.UUID) and isinstance(current, str):
                try:
                    current = uuid.UUID(current)
                except (ValueError, AttributeError):
                    pass
        if current != new_value:
            return True
    return False


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

    stats = {
        'created': 0, 'updated': 0, 'unchanged': 0, 'deleted': 0,
        'errors': [], 'items_detail': [],
    }
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
                    'cover_image_url': rewrite_cover_image_url(
                        metadata.get('cover_image', '') or metadata.get('cover_image_url', ''),
                        source, rel_path,
                    ),
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

                # Pre-derive description so the no-change comparison below
                # matches what Article.save() would persist. Article.save()
                # auto-fills description from the first 200 chars of
                # content_markdown when description is empty; if we left
                # defaults['description'] = '' we'd see a spurious diff on
                # every re-sync (issue #225).
                if not defaults['description'] and body:
                    defaults['description'] = body[:200]

                # Issue #225: only mark as 'updated' when content actually
                # changed. Look up first; if found and unchanged, skip the
                # save and don't bump items_updated / items_detail.
                try:
                    article = Article.objects.get(
                        slug=current_slug, source_repo=source.repo_name,
                    )
                except Article.DoesNotExist:
                    article = Article(
                        slug=current_slug, **defaults,
                    )
                    article.save()
                    created = True
                    changed = True
                else:
                    if _defaults_differ(article, defaults):
                        for k, v in defaults.items():
                            setattr(article, k, v)
                        article.save()
                        created = False
                        changed = True
                    else:
                        created = False
                        changed = False

                # Expand widgets after save (save already rendered markdown to HTML)
                # Only re-run when we actually saved — for an unchanged row the
                # rendered HTML is already correct.
                if changed and article.data_json:
                    from content.utils.widgets import expand_widgets
                    expanded = expand_widgets(article.content_html, article.data_json)
                    Article.objects.filter(pk=article.pk).update(content_html=expanded)

                if not changed:
                    stats['unchanged'] += 1
                    continue

                action = 'created' if created else 'updated'
                if created:
                    stats['created'] += 1
                else:
                    stats['updated'] += 1
                stats['items_detail'].append({
                    'title': defaults['title'],
                    'slug': current_slug,
                    'action': action,
                    'content_type': 'article',
                })

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

    for article in stale_articles:
        stats['items_detail'].append({
            'title': article.title,
            'slug': article.slug,
            'action': 'deleted',
            'content_type': 'article',
        })
    deleted_count = stale_articles.count()
    stale_articles.update(published=False, status='draft')
    stats['deleted'] = deleted_count

    return stats


def _sync_single_course(
    course_dir, repo_dir, source, commit_sha, stats,
    seen_course_slugs, failed_course_slugs, known_images=None,
):
    """Parse one course.yaml + module dirs into a Course with Modules/Units.

    Used by both multi-course mode (each child dir is its own course) and
    single-course mode (the resolved content_dir is the course root).

    Respects ``ignore:`` in ``course.yaml`` (a list of globs relative to the
    course root) — matched files are skipped everywhere in the course. If no
    ``description:`` is set in ``course.yaml`` and ``README.md`` exists at the
    course root and is not ignored, the README body becomes the course
    description.
    """
    from content.models import Course

    course_yaml_path = os.path.join(course_dir, 'course.yaml')
    course_data = None
    try:
        course_data = _parse_yaml_file(course_yaml_path)
        slug = course_data.get('slug', os.path.basename(course_dir.rstrip(os.sep)))
        rel_path = os.path.relpath(course_dir, repo_dir)

        # Edge Case 7: Frontmatter validation
        _validate_frontmatter(course_data, 'course', rel_path)

        # Require content_id in frontmatter
        course_content_id = course_data.get('content_id')
        if not course_content_id:
            msg = f'Skipping {rel_path}: missing content_id in frontmatter'
            logger.warning(msg)
            stats['errors'].append({'file': rel_path, 'error': msg})
            return

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
            return

        seen_course_slugs.add(slug)

        # Course-level ignore globs (relative to course_dir). Applied to every
        # module via _sync_course_modules.
        raw_ignore = course_data.get('ignore', []) or []
        course_ignore_patterns = [str(p) for p in raw_ignore]

        # Description: explicit `description:` wins; otherwise fall back to
        # README.md at the course root if present and not ignored.
        description = course_data.get('description', '') or ''
        if not description:
            readme_path = os.path.join(course_dir, 'README.md')
            if (
                os.path.isfile(readme_path)
                and not _matches_ignore_patterns(
                    'README.md', course_ignore_patterns,
                )
            ):
                try:
                    _, readme_body = _parse_markdown_file(readme_path)
                    if readme_body and readme_body.strip():
                        description = readme_body
                except Exception as e:
                    logger.warning(
                        'Failed to read course README at %s: %s',
                        readme_path, e,
                    )

        course_defaults = {
            'title': course_data.get('title', slug),
            'description': description,
            'instructor_name': course_data.get('instructor_name', ''),
            'instructor_bio': course_data.get('instructor_bio', ''),
            'cover_image_url': rewrite_cover_image_url(
                course_data.get('cover_image', '') or course_data.get('cover_image_url', ''),
                source, os.path.join(rel_path, 'course.yaml'),
            ),
            'required_level': course_data.get('required_level', 0),
            'discussion_url': course_data.get('discussion_url', ''),
            'tags': course_data.get('tags', []),
            'testimonials': course_data.get('testimonials', []),
            'status': 'published',
            'source_repo': source.repo_name,
            'source_path': rel_path,
            'source_commit': commit_sha,
            'content_id': course_content_id,
        }

        # Prefer the stable content_id over slug when locating an existing
        # course row. This lets authors rename a course (slug/title/source
        # path) without triggering a duplicate content_id insert.
        course = Course.objects.filter(
            content_id=course_content_id,
            source_repo=source.repo_name,
        ).first()

        # Backward-compat fallback: older synced rows may predate content_id
        # backfills, so still support slug-based matching when the stable-ID
        # lookup misses.
        if course is None:
            course = Course.objects.filter(
                slug=slug,
                source_repo=source.repo_name,
            ).first()

        if course is None:
            course = Course(slug=slug, **course_defaults)
            course.save()
            created = True
            changed = True
        else:
            identity_changed = (
                course.slug != slug
                or course.source_path != rel_path
            )
            if identity_changed or _defaults_differ(course, course_defaults):
                course.slug = slug
                for k, v in course_defaults.items():
                    setattr(course, k, v)
                course.save()
                created = False
                changed = True
            else:
                created = False
                changed = False

        if changed:
            action = 'created' if created else 'updated'
            if created:
                stats['created'] += 1
            else:
                stats['updated'] += 1
            stats['items_detail'].append({
                'title': course_defaults.get('title', slug),
                'slug': slug,
                'action': action,
                'content_type': 'course',
                'course_id': course.pk,
                'course_slug': course.slug,
            })
        else:
            stats['unchanged'] += 1

        # Sync modules (immediate child directories of course_dir)
        _sync_course_modules(
            course, course_dir, repo_dir, source.repo_name,
            commit_sha, stats, known_images=known_images,
            course_ignore_patterns=course_ignore_patterns,
        )

    except Exception as e:
        try:
            failed_slug = (course_data or {}).get(
                'slug', os.path.basename(course_dir.rstrip(os.sep)),
            )
        except Exception:
            failed_slug = os.path.basename(course_dir.rstrip(os.sep))
        failed_course_slugs.add(failed_slug)
        stats['errors'].append({
            'file': os.path.relpath(course_yaml_path, repo_dir),
            'error': str(e),
        })
        logger.warning(
            'Error syncing course %s: %s',
            os.path.basename(course_dir.rstrip(os.sep)), e,
        )


def _sync_courses(source, repo_dir, commit_sha, sync_log, known_images=None):
    """Sync courses with modules and units from the repo.

    Two modes:

    - Single-course mode: if ``course.yaml`` exists at ``repo_dir`` root, the
      whole repo_dir is treated as one course. Modules are immediate child
      directories. This wins over multi-course mode if both shapes are
      present (any child course.yaml files are ignored - those child dirs
      are interpreted as modules, and skipped if they have no module.yaml).
    - Multi-course mode: otherwise, each child directory containing a
      ``course.yaml`` is processed as its own course (legacy behavior used
      by the AI-Shipping-Labs/content monorepo's ``courses/`` subtree).
    """
    from content.models import Course

    stats = {
        'created': 0, 'updated': 0, 'unchanged': 0, 'deleted': 0,
        'errors': [], 'items_detail': [],
    }
    seen_course_slugs = set()
    failed_course_slugs = set()

    root_course_yaml = os.path.join(repo_dir, 'course.yaml')
    if os.path.exists(root_course_yaml):
        # Single-course mode: repo_dir IS the course directory.
        _sync_single_course(
            repo_dir, repo_dir, source, commit_sha, stats,
            seen_course_slugs, failed_course_slugs, known_images=known_images,
        )
    else:
        # Multi-course mode: each child dir with course.yaml is a course.
        for entry in os.scandir(repo_dir):
            if not entry.is_dir() or entry.name.startswith('.'):
                continue

            course_yaml_path = os.path.join(entry.path, 'course.yaml')
            if not os.path.exists(course_yaml_path):
                continue

            _sync_single_course(
                entry.path, repo_dir, source, commit_sha, stats,
                seen_course_slugs, failed_course_slugs,
                known_images=known_images,
            )

    # Edge Case 3: Exclude failed slugs from stale-content cleanup
    stale_courses = Course.objects.filter(
        source_repo=source.repo_name,
        status='published',
    ).exclude(slug__in=seen_course_slugs).exclude(slug__in=failed_course_slugs)

    for course in stale_courses:
        stats['items_detail'].append({
            'title': course.title,
            'slug': course.slug,
            'action': 'deleted',
            'content_type': 'course',
            'course_id': course.pk,
            'course_slug': course.slug,
        })
    deleted_count = stale_courses.count()
    stale_courses.update(status='draft')
    stats['deleted'] += deleted_count

    return stats


def _build_course_unit_lookup(course_dir, course_ignore_patterns=None):
    """Build a ``{module_slug: {filename: unit_slug}}`` map for a course tree.

    Used by the markdown link rewriter (issue #226) so we can resolve sibling
    and cross-module ``.md`` links without doing a database round-trip per
    link. The slug derivation here mirrors what :func:`_sync_module_units`
    writes to ``Module.slug`` / ``Unit.slug`` so the rewriter produces URLs
    that actually resolve.

    Modules without a ``module.yaml`` are skipped (mirroring
    :func:`_sync_course_modules`). Files starting with ``.`` are ignored.
    Frontmatter ``slug`` overrides for both modules and units are honoured.

    To stay in lock-step with :func:`_sync_module_units` (issue #233),
    files matched by course-level or module-level ``ignore:`` globs are
    excluded, and non-README files missing ``content_id`` in frontmatter
    are excluded too. Without these checks the rewriter would emit
    "working-looking" URLs to units that were never persisted, producing
    silent 404s instead of the standard unresolvable-link warning.

    Args:
        course_dir: Path to the course root directory on disk.
        course_ignore_patterns: List of glob patterns from the course-level
            ``ignore:`` key, relative to ``course_dir``. Same shape as the
            value passed to :func:`_sync_course_modules`.
    """
    lookup = {}
    if not os.path.isdir(course_dir):
        return lookup

    course_ignore_patterns = course_ignore_patterns or []

    for entry in sorted(os.scandir(course_dir), key=lambda e: e.name):
        if (
            not entry.is_dir()
            or entry.name.startswith('.')
            or entry.name == 'images'
        ):
            continue

        # Mirror _sync_course_modules: a module dir matched by a course-level
        # ignore glob (e.g. ``docs/**``) is skipped entirely — its files
        # never become Units, so they must not appear in the lookup.
        dir_rel_to_course = os.path.relpath(entry.path, course_dir)
        if _matches_ignore_patterns(dir_rel_to_course, course_ignore_patterns):
            continue

        module_yaml_path = os.path.join(entry.path, 'module.yaml')
        if not os.path.exists(module_yaml_path):
            continue

        # Best-effort: skip modules whose YAML can't be parsed. We don't want
        # link rewriting to ever fail the sync, so a parse error here just
        # means those modules' units can't be link targets.
        try:
            module_data = _parse_yaml_file(module_yaml_path) or {}
        except Exception:
            module_data = {}
        module_slug = module_data.get('slug') or derive_slug(entry.name)

        # Module-level ignore patterns are relative to the module dir,
        # course-level patterns are relative to the course dir — same split
        # _sync_module_units uses.
        raw_module_ignore = module_data.get('ignore', []) or []
        module_ignore_patterns = [str(p) for p in raw_module_ignore]

        files = {}
        for filename in os.listdir(entry.path):
            if (
                not filename.lower().endswith('.md')
                or filename.startswith('.')
            ):
                continue
            filepath = os.path.join(entry.path, filename)
            if not os.path.isfile(filepath):
                continue

            # Same _is_ignored check _sync_module_units uses: a file matched
            # by either glob list is skipped from sync, so it must also be
            # skipped from the lookup.
            rel_to_course = os.path.relpath(filepath, course_dir)
            if _matches_ignore_patterns(
                rel_to_course, course_ignore_patterns,
            ):
                continue
            if _matches_ignore_patterns(filename, module_ignore_patterns):
                continue

            try:
                metadata, _ = _parse_markdown_file(filepath)
            except Exception:
                metadata = {}

            if filename.lower() == 'readme.md':
                # README is the module overview, not a unit (issue #222).
                # We still register it under a sentinel slug so the link
                # rewriter can spot README.md targets and emit module-overview
                # URLs (handled in content/utils/md_links.py). README has no
                # content_id requirement (the sync derives one).
                unit_slug = '__module_overview__'
            else:
                # _sync_module_units skips non-README files missing
                # content_id (logs a warning, no Unit created). Mirror that
                # here so the rewriter doesn't emit URLs for ghost units.
                if not metadata.get('content_id'):
                    continue
                # Key-absent default to match _sync_module_units exactly:
                # an explicit empty ``slug:`` in YAML yields ``''`` rather
                # than falling back to the filename-derived slug. In
                # practice authors never write ``slug:`` empty.
                unit_slug = metadata.get('slug', derive_slug(filename))
            files[filename] = unit_slug

        lookup[module_slug] = files

    return lookup


def _sync_course_modules(course, course_dir, repo_dir, repo_name, commit_sha, stats,
                         known_images=None, course_ignore_patterns=None):
    """Sync modules and units for a course.

    ``course_ignore_patterns`` are globs relative to ``course_dir`` from the
    course-level ``ignore:`` key. A directory whose path matches is skipped
    entirely. The patterns are also passed down to unit sync so individual
    files matched at the course level are skipped wherever they appear.
    """
    from content.models import Module

    course_ignore_patterns = course_ignore_patterns or []
    seen_module_paths = set()

    # Build the course-wide unit lookup once before processing any unit so the
    # markdown link rewriter (issue #226) can resolve sibling and cross-module
    # `.md` links to platform URLs. Pass course-level ignore patterns so the
    # lookup mirrors what _sync_module_units actually persists (issue #233).
    unit_lookup = _build_course_unit_lookup(
        course_dir, course_ignore_patterns=course_ignore_patterns,
    )

    for entry in sorted(os.scandir(course_dir), key=lambda e: e.name):
        if not entry.is_dir() or entry.name.startswith('.') or entry.name == 'images':
            continue

        # Skip whole module dirs that match course-level ignore globs
        # (e.g. `docs/**` ignores the docs/ directory in addition to its files).
        dir_rel_to_course = os.path.relpath(entry.path, course_dir)
        if _matches_ignore_patterns(dir_rel_to_course, course_ignore_patterns):
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

            module_defaults = {
                'title': module_data.get('title', entry.name),
                'slug': slug,
                'sort_order': sort_order,
                'source_repo': repo_name,
                'source_commit': commit_sha,
            }
            # Issue #225: only count as 'updated' when content actually changed.
            try:
                module = Module.objects.get(
                    course=course, source_path=rel_path,
                )
            except Module.DoesNotExist:
                module = Module(
                    course=course, source_path=rel_path, **module_defaults,
                )
                module.save()
                created = True
                changed = True
            else:
                if _defaults_differ(module, module_defaults):
                    for k, v in module_defaults.items():
                        setattr(module, k, v)
                    module.save()
                    created = False
                    changed = True
                else:
                    created = False
                    changed = False

            if changed:
                action = 'created' if created else 'updated'
                if created:
                    stats['created'] += 1
                else:
                    stats['updated'] += 1
                # Per-level breakdown (issue #224): track each module touched
                # so the dashboard can show "Modules: X created Y updated"
                # and link to the studio edit page.
                stats['items_detail'].append({
                    'title': module.title,
                    'slug': module.slug,
                    'action': action,
                    'content_type': 'module',
                    'course_id': course.pk,
                    'course_slug': course.slug,
                    'module_id': module.pk,
                })
            else:
                stats['unchanged'] += 1

            # Module-level ignore patterns (relative to module dir). Course
            # patterns are translated/filtered separately in _sync_module_units.
            raw_module_ignore = module_data.get('ignore', []) or []
            module_ignore_patterns = [str(p) for p in raw_module_ignore]

            # Sync units within this module
            _sync_module_units(
                module, entry.path, repo_dir, repo_name, commit_sha, stats,
                known_images=known_images,
                course_dir=course_dir,
                course_ignore_patterns=course_ignore_patterns,
                module_ignore_patterns=module_ignore_patterns,
                course_slug=course.slug,
                unit_lookup=unit_lookup,
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
                       known_images=None, course_dir=None,
                       course_ignore_patterns=None,
                       module_ignore_patterns=None,
                       course_slug=None, unit_lookup=None):
    """Sync units (markdown files) within a module directory.

    ``course_ignore_patterns`` are globs relative to ``course_dir`` (course
    root). ``module_ignore_patterns`` are globs relative to ``module_dir``.
    Files matched by either list are skipped.

    README.md at the module root is the module's overview (issue #222):
    its body is written to ``Module.overview`` and rendered into
    ``Module.overview_html``. The README does NOT become a Unit, so it is
    not counted in lesson totals and does not appear in the lesson list.
    The page at ``/courses/<course>/<module>/`` renders the overview.

    ``course_slug`` and ``unit_lookup`` are used by the markdown link
    rewriter (issue #226) to convert intra-content ``.md`` links into
    platform URLs. When either is missing, link rewriting is skipped.
    """
    from content.models import Unit, UserCourseProgress
    from content.utils.md_links import rewrite_md_links

    course_ignore_patterns = course_ignore_patterns or []
    module_ignore_patterns = module_ignore_patterns or []
    # course_dir defaults to module_dir's parent when not supplied so callers
    # that pre-date this signature still work (course-level patterns become
    # no-ops in that case because course_ignore_patterns is empty).
    if course_dir is None:
        course_dir = os.path.dirname(module_dir)

    seen_unit_paths = set()
    # Track newly created units with their hashes for rename detection
    new_unit_hashes = {}

    def _is_ignored(filename):
        """Return True if the file is matched by any course- or module-level ignore glob."""
        filepath = os.path.join(module_dir, filename)
        rel_to_course = os.path.relpath(filepath, course_dir)
        if _matches_ignore_patterns(rel_to_course, course_ignore_patterns):
            return True
        if _matches_ignore_patterns(filename, module_ignore_patterns):
            return True
        return False

    # README at module root -> Module.overview (issue #222), unless ignored.
    readme_filename = None
    for name in os.listdir(module_dir):
        if name.lower() == 'readme.md':
            readme_filename = name
            break

    if readme_filename and not _is_ignored(readme_filename):
        readme_path = os.path.join(module_dir, readme_filename)
        readme_rel = os.path.relpath(readme_path, repo_dir)
        try:
            _metadata, body = _parse_markdown_file(readme_path)

            base_dir = os.path.dirname(readme_rel)
            if known_images is not None:
                _check_broken_image_refs(
                    body, readme_rel, repo_name, base_dir,
                    known_images, stats.get('errors', []),
                )
            body = rewrite_image_urls(body, repo_name, base_dir)
            # Rewrite intra-content `.md` links to platform URLs (issue #226).
            if course_slug and unit_lookup is not None:
                body = rewrite_md_links(
                    body,
                    course_slug=course_slug,
                    module_slug=module.slug,
                    unit_lookup=unit_lookup,
                    source_path=readme_rel,
                    sync_errors=stats.get('errors'),
                )

            overview_changed = (
                module.overview != body
                or module.overview_source_path != readme_rel
            )
            if overview_changed:
                module.overview = body
                module.overview_source_path = readme_rel
                module.save(update_fields=[
                    'overview', 'overview_html', 'overview_source_path',
                ])
                # Issue #224: surface the README touch in the per-level
                # breakdown so staff can see at a glance that the module
                # overview changed. Reported as content_type='module'
                # (not 'unit'), since README is no longer a Unit.
                stats['items_detail'].append({
                    'title': f'{module.title} — overview',
                    'slug': module.slug,
                    'action': 'updated',
                    'content_type': 'module',
                    'course_id': module.course_id,
                    'course_slug': course_slug or module.course.slug,
                    'module_id': module.pk,
                })
            else:
                # Unchanged README still counts as a synced file — track it
                # in the no-change bucket so the dashboard shows accurate
                # totals (issue #225).
                stats['unchanged'] += 1
        except Exception as e:
            stats['errors'].append({
                'file': readme_rel,
                'error': str(e),
            })
    elif not readme_filename and module.overview:
        # README was removed from the repo: clear the overview so the page
        # falls back to the lesson-list-only layout.
        module.overview = ''
        module.overview_source_path = None
        module.save(update_fields=[
            'overview', 'overview_html', 'overview_source_path',
        ])

    # Defensive cleanup: if a legacy README-as-unit row still exists for
    # this module (e.g. running sync against a DB whose backfill migration
    # already ran but the unit got recreated), drop it. Identified by the
    # exact slug/sort_order pair the old sync wrote.
    Unit.objects.filter(
        module=module, slug='readme', sort_order=-1,
    ).delete()

    for filename in sorted(os.listdir(module_dir)):
        if not filename.endswith('.md') or filename.upper() == 'README.MD':
            continue

        # Respect course- and module-level ignore globs.
        if _is_ignored(filename):
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
            # Rewrite intra-content `.md` links to platform URLs (issue #226).
            if course_slug and unit_lookup is not None:
                body = rewrite_md_links(
                    body,
                    course_slug=course_slug,
                    module_slug=module.slug,
                    unit_lookup=unit_lookup,
                    source_path=rel_path,
                    sync_errors=stats.get('errors'),
                )

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

            # Issue #225: only count as 'updated' when content actually changed.
            try:
                unit = Unit.objects.get(
                    module=module, source_path=rel_path,
                )
            except Unit.DoesNotExist:
                unit = Unit(
                    module=module, source_path=rel_path, **defaults,
                )
                unit.save()
                created = True
                changed = True
            else:
                if _defaults_differ(unit, defaults):
                    for k, v in defaults.items():
                        setattr(unit, k, v)
                    unit.save()
                    created = False
                    changed = True
                else:
                    created = False
                    changed = False

            if not changed:
                stats['unchanged'] += 1
                continue

            action = 'created' if created else 'updated'
            if created:
                stats['created'] += 1
                new_unit_hashes[content_hash] = unit
            else:
                stats['updated'] += 1
            # Per-level breakdown (issue #224): track each unit touched
            # so the dashboard can show "Lessons (units): X created Y updated"
            # and link to the studio edit page.
            stats['items_detail'].append({
                'title': unit.title,
                'slug': unit.slug,
                'action': action,
                'content_type': 'unit',
                'course_id': module.course_id,
                'course_slug': course_slug or module.course.slug,
                'module_id': module.pk,
                'module_slug': module.slug,
                'unit_id': unit.pk,
            })

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
    """Sync resources: curated links and downloads.

    Note: recordings are now synced via _sync_events (content_type='event').
    """

    stats = {
        'created': 0, 'updated': 0, 'unchanged': 0, 'deleted': 0,
        'errors': [], 'items_detail': [],
    }

    # Sync curated links
    links_dir = os.path.join(repo_dir, 'curated-links')
    if os.path.isdir(links_dir):
        _sync_curated_links(
            source, links_dir, repo_dir, commit_sha, stats,
        )

    # Sync downloads
    downloads_dir = os.path.join(repo_dir, 'downloads')
    if os.path.isdir(downloads_dir):
        _sync_downloads(
            source, downloads_dir, repo_dir, commit_sha, stats,
        )

    return stats


def _event_requests_zoom_meeting(data):
    """Return True when synced frontmatter requests a Zoom-backed event."""
    location = str(data.get('location', '') or '').strip().lower()
    platform = str(data.get('platform', '') or '').strip().lower()
    return location == 'zoom' or platform == 'zoom'


def _coerce_event_datetime(value):
    """Convert synced event frontmatter values into aware datetimes."""
    import datetime as dt

    if value in (None, ''):
        return None

    if isinstance(value, dt.datetime):
        if timezone.is_naive(value):
            return timezone.make_aware(value, dt.timezone.utc)
        return value

    if isinstance(value, dt.date):
        return dt.datetime.combine(value, dt.time.min, tzinfo=dt.timezone.utc)

    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            return None
        if normalized.endswith('Z'):
            normalized = f'{normalized[:-1]}+00:00'
        try:
            parsed = dt.datetime.fromisoformat(normalized)
        except ValueError:
            parsed_date = dt.date.fromisoformat(normalized)
            return dt.datetime.combine(
                parsed_date, dt.time.min, tzinfo=dt.timezone.utc,
            )
        if timezone.is_naive(parsed):
            return timezone.make_aware(parsed, dt.timezone.utc)
        return parsed

    raise ValueError(f'Unsupported event datetime value: {value!r}')


def _maybe_create_zoom_meeting_for_synced_event(event, data):
    """Best-effort Zoom meeting creation for newly synced events."""
    if event.event_type != 'live' or event.status == 'completed':
        return

    if not data.get('start_datetime') or not _event_requests_zoom_meeting(data):
        return

    if event.zoom_meeting_id or event.zoom_join_url:
        return

    from integrations.services.zoom import ZoomAPIError, create_meeting

    try:
        result = create_meeting(event)
    except (ZoomAPIError, requests.RequestException) as exc:
        logger.warning(
            'Failed to auto-create Zoom meeting for synced event %s: %s',
            event.slug,
            exc,
        )
        return

    event.zoom_meeting_id = result['meeting_id']
    event.zoom_join_url = result['join_url']
    event.save(update_fields=['zoom_meeting_id', 'zoom_join_url'])


def _sync_events(source, repo_dir, commit_sha, sync_log, known_images=None):
    """Sync event YAML/markdown files from the events/ directory.

    Upserts into the Event model. Only updates content fields; operational
    fields (start_datetime, zoom_join_url, status, etc.) are never overwritten
    by sync if the event already exists.
    """
    import datetime as dt

    from events.models import Event

    stats = {
        'created': 0, 'updated': 0, 'unchanged': 0, 'deleted': 0,
        'errors': [], 'items_detail': [],
    }
    seen_slugs = set()
    failed_slugs = set()

    events_dir = repo_dir  # content_path already resolved by caller

    for filename in os.listdir(events_dir):
        if not (filename.endswith('.yaml') or filename.endswith('.yml') or filename.endswith('.md')):
            continue

        filepath = os.path.join(events_dir, filename)
        rel_path = os.path.relpath(filepath, repo_dir)

        try:
            # Parse YAML or markdown with frontmatter
            if filename.endswith('.md'):
                data, body = _parse_markdown_file(filepath)
                if body and body.strip():
                    data['description'] = body.strip()
            else:
                data = _parse_yaml_file(filepath)

            slug = data.get('slug', os.path.splitext(filename)[0])

            # Require content_id
            event_content_id = data.get('content_id')
            if not event_content_id:
                msg = f'Skipping {rel_path}: missing content_id in frontmatter'
                logger.warning(msg)
                stats['errors'].append({'file': rel_path, 'error': msg})
                continue

            # Slug collision check
            if _check_slug_collision(Event, slug, source.repo_name, rel_path):
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

            # Validate recap if present: must be a dict
            recap_value = data.get('recap', {})
            if recap_value and not isinstance(recap_value, dict):
                msg = (
                    f'Invalid recap in {rel_path}: must be a mapping/dict, '
                    f'got {type(recap_value).__name__}. Skipping recap.'
                )
                logger.warning(msg)
                stats['errors'].append({'file': rel_path, 'error': msg})
                recap_value = {}

            # Content fields that sync always updates
            content_defaults = {
                'title': data.get('title', slug),
                'description': data.get('description', ''),
                'recording_url': data.get('recording_url', '') or data.get('video_url', ''),
                'recording_embed_url': data.get('google_embed_url', ''),
                'transcript_url': data.get('transcript_url', ''),
                'timestamps': data.get('timestamps', []),
                'materials': data.get('materials', []),
                'core_tools': data.get('core_tools', []),
                'learning_objectives': data.get('learning_objectives', []),
                'outcome': data.get('outcome', ''),
                'tags': data.get('tags', []),
                'required_level': data.get('required_level', 0),
                'speaker_name': data.get('speaker_name', ''),
                'speaker_bio': data.get('speaker_bio', ''),
                'related_course': data.get('related_course', ''),
                'published': data.get('published', True),
                'recap': recap_value,
                'content_id': event_content_id,
                'source_repo': source.repo_name,
                'source_path': rel_path,
                'source_commit': commit_sha,
            }

            # Handle cover_image
            cover_image = data.get('cover_image', '')
            if cover_image:
                content_defaults['cover_image_url'] = rewrite_cover_image_url(
                    cover_image, source, rel_path,
                )

            # Handle published_at
            published_at = data.get('published_at')
            if published_at:
                if isinstance(published_at, str):
                    content_defaults['published_at'] = dt.datetime.combine(
                        dt.date.fromisoformat(published_at),
                        dt.time.min,
                        tzinfo=dt.timezone.utc,
                    )
                elif isinstance(published_at, (dt.date, dt.datetime)):
                    if isinstance(published_at, dt.date) and not isinstance(published_at, dt.datetime):
                        content_defaults['published_at'] = dt.datetime.combine(
                            published_at, dt.time.min, tzinfo=dt.timezone.utc,
                        )
                    else:
                        content_defaults['published_at'] = published_at

            # Try to find existing event by slug + source_repo
            try:
                event = Event.objects.get(slug=slug, source_repo=source.repo_name)
                # Issue #225: skip the save when every synced content field
                # matches the DB row. Operational fields (start_datetime,
                # zoom_join_url, status, etc.) are not in content_defaults,
                # so they are never touched here.
                if _defaults_differ(event, content_defaults):
                    for key, value in content_defaults.items():
                        setattr(event, key, value)
                    event.save()
                    stats['updated'] += 1
                    stats['items_detail'].append({
                        'title': content_defaults.get('title', slug),
                        'slug': slug,
                        'action': 'updated',
                        'content_type': 'event',
                    })
                else:
                    stats['unchanged'] += 1
            except Event.DoesNotExist:
                # Content-repo events still default to recording-style rows
                # unless operational frontmatter explicitly says otherwise.
                start_dt_value = _coerce_event_datetime(data.get('start_datetime'))
                if not start_dt_value:
                    start_dt_value = _coerce_event_datetime(published_at)
                if not start_dt_value:
                    start_dt_value = timezone.now()

                event = Event(
                    slug=slug,
                    start_datetime=start_dt_value,
                    end_datetime=_coerce_event_datetime(data.get('end_datetime')),
                    status=data.get('status') or 'completed',
                    event_type=data.get('event_type') or 'live',
                    timezone=data.get('timezone') or settings.TIME_ZONE,
                    platform=data.get('platform') or 'zoom',
                    location=data.get('location', '') or '',
                    **content_defaults,
                )
                event.save()
                stats['created'] += 1
                stats['items_detail'].append({
                    'title': content_defaults.get('title', slug),
                    'slug': slug,
                    'action': 'created',
                    'content_type': 'event',
                })
                _maybe_create_zoom_meeting_for_synced_event(event, data)

        except Exception as e:
            fallback_slug = os.path.splitext(filename)[0]
            try:
                failed_slug = data.get('slug', fallback_slug)
            except Exception:
                failed_slug = fallback_slug
            failed_slugs.add(failed_slug)
            stats['errors'].append({'file': rel_path, 'error': str(e)})

    # Soft-delete: if a synced event file is removed, set published=False
    stale = Event.objects.filter(
        source_repo=source.repo_name,
        published=True,
    ).exclude(slug__in=seen_slugs).exclude(slug__in=failed_slugs)
    for ev in stale:
        stats['items_detail'].append({
            'title': ev.title,
            'slug': ev.slug,
            'action': 'deleted',
            'content_type': 'event',
        })
    deleted_count = stale.count()
    stale.update(published=False)
    stats['deleted'] += deleted_count

    return stats


def _sync_curated_links(source, links_dir, repo_dir, commit_sha, stats):
    """Sync curated links from individual markdown files."""
    from content.models import CuratedLink

    seen_item_ids = set()
    failed_item_ids = set()

    for filename in os.listdir(links_dir):
        if not filename.endswith('.md'):
            continue

        filepath = os.path.join(links_dir, filename)
        rel_path = os.path.relpath(filepath, repo_dir)

        try:
            metadata, body = _parse_markdown_file(filepath)

            # Edge Case 7: Frontmatter validation
            # Map content_id to item_id for validation
            if 'content_id' in metadata and 'item_id' not in metadata:
                metadata['item_id'] = metadata['content_id']

            _validate_frontmatter(metadata, 'curated_link', rel_path)

            item_id = metadata.get('item_id')
            if not item_id:
                msg = f'Skipping {rel_path}: missing content_id/item_id in frontmatter'
                logger.warning(msg)
                stats['errors'].append({'file': rel_path, 'error': msg})
                continue

            seen_item_ids.add(item_id)

            # Use body text as description, fall back to frontmatter description
            description = body.strip() if body.strip() else metadata.get('description', '')

            defaults = {
                'title': metadata.get('title', ''),
                'description': description,
                'url': metadata.get('url', ''),
                'category': metadata.get('category', 'other'),
                'tags': metadata.get('tags', []),
                'sort_order': metadata.get('sort_order', 0),
                'required_level': metadata.get('required_level', 0),
                'published': metadata.get('published', True),
                'source_repo': source.repo_name,
                'source_path': rel_path,
                'source_commit': commit_sha,
            }

            # Issue #225: only count as 'updated' when content actually changed.
            try:
                obj = CuratedLink.objects.get(item_id=item_id)
            except CuratedLink.DoesNotExist:
                obj = CuratedLink(item_id=item_id, **defaults)
                obj.save()
                created = True
                changed = True
            else:
                if _defaults_differ(obj, defaults):
                    for k, v in defaults.items():
                        setattr(obj, k, v)
                    obj.save()
                    created = False
                    changed = True
                else:
                    created = False
                    changed = False

            if not changed:
                stats['unchanged'] += 1
                continue

            action = 'created' if created else 'updated'
            if created:
                stats['created'] += 1
            else:
                stats['updated'] += 1
            stats['items_detail'].append({
                'title': defaults['title'],
                'slug': item_id,
                'action': action,
                'content_type': 'resource',
            })

        except Exception as e:
            fallback_id = os.path.splitext(filename)[0]
            try:
                failed_id = metadata.get('item_id', fallback_id)
            except Exception:
                failed_id = fallback_id
            failed_item_ids.add(failed_id)
            stats['errors'].append({'file': rel_path, 'error': str(e)})

    # Soft-delete stale links from this repo, excluding failed items
    stale = CuratedLink.objects.filter(
        source_repo=source.repo_name,
        published=True,
    ).exclude(item_id__in=seen_item_ids).exclude(item_id__in=failed_item_ids)
    for link in stale:
        stats['items_detail'].append({
            'title': link.title,
            'slug': link.item_id,
            'action': 'deleted',
            'content_type': 'resource',
        })
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
                'cover_image_url': rewrite_cover_image_url(
                    data.get('cover_image', '') or data.get('cover_image_url', ''),
                    source, rel_path,
                ),
                'tags': data.get('tags', []),
                'required_level': data.get('required_level', 0),
                'published': True,
                'source_repo': source.repo_name,
                'source_path': rel_path,
                'source_commit': commit_sha,
                'content_id': download_content_id,
            }

            # Issue #225: only count as 'updated' when content actually changed.
            try:
                download = Download.objects.get(
                    slug=slug, source_repo=source.repo_name,
                )
            except Download.DoesNotExist:
                download = Download(slug=slug, **defaults)
                download.save()
                created = True
                changed = True
            else:
                if _defaults_differ(download, defaults):
                    for k, v in defaults.items():
                        setattr(download, k, v)
                    download.save()
                    created = False
                    changed = True
                else:
                    created = False
                    changed = False

            if not changed:
                stats['unchanged'] += 1
                continue

            action = 'created' if created else 'updated'
            if created:
                stats['created'] += 1
            else:
                stats['updated'] += 1
            stats['items_detail'].append({
                'title': defaults['title'],
                'slug': slug,
                'action': action,
                'content_type': 'resource',
            })

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
    for dl in stale:
        stats['items_detail'].append({
            'title': dl.title,
            'slug': dl.slug,
            'action': 'deleted',
            'content_type': 'resource',
        })
    deleted_count = stale.count()
    stale.update(published=False)
    stats['deleted'] += deleted_count


def _sync_projects(source, repo_dir, commit_sha, sync_log, known_images=None):
    """Sync project markdown files from the repo."""
    from datetime import date as date_type

    from content.models import Project

    stats = {
        'created': 0, 'updated': 0, 'unchanged': 0, 'deleted': 0,
        'errors': [], 'items_detail': [],
    }
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
                    'cover_image_url': rewrite_cover_image_url(
                        metadata.get('cover_image', '') or metadata.get('cover_image_url', ''),
                        source, rel_path,
                    ),
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

                # Issue #225: only count as 'updated' when content actually changed.
                try:
                    project = Project.objects.get(
                        slug=slug, source_repo=source.repo_name,
                    )
                except Project.DoesNotExist:
                    project = Project(slug=slug, **defaults)
                    project.save()
                    created = True
                    changed = True
                else:
                    if _defaults_differ(project, defaults):
                        for k, v in defaults.items():
                            setattr(project, k, v)
                        project.save()
                        created = False
                        changed = True
                    else:
                        created = False
                        changed = False

                if not changed:
                    stats['unchanged'] += 1
                    continue

                action = 'created' if created else 'updated'
                if created:
                    stats['created'] += 1
                else:
                    stats['updated'] += 1
                stats['items_detail'].append({
                    'title': defaults['title'],
                    'slug': slug,
                    'action': action,
                    'content_type': 'project',
                })

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
    for proj in stale:
        stats['items_detail'].append({
            'title': proj.title,
            'slug': proj.slug,
            'action': 'deleted',
            'content_type': 'project',
        })
    deleted_count = stale.count()
    stale.update(published=False, status='pending_review')
    stats['deleted'] = deleted_count

    return stats


def _sync_interview_questions(source, repo_dir, commit_sha, sync_log, known_images=None):
    """Sync interview question categories from markdown files."""
    from content.models import InterviewCategory

    stats = {
        'created': 0, 'updated': 0, 'unchanged': 0, 'deleted': 0,
        'errors': [], 'items_detail': [],
    }
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

                # Issue #225: only count as 'updated' when content actually changed.
                try:
                    obj = InterviewCategory.objects.get(slug=slug)
                except InterviewCategory.DoesNotExist:
                    obj = InterviewCategory(slug=slug, **defaults)
                    obj.save()
                    created = True
                    changed = True
                else:
                    if _defaults_differ(obj, defaults):
                        for k, v in defaults.items():
                            setattr(obj, k, v)
                        obj.save()
                        created = False
                        changed = True
                    else:
                        created = False
                        changed = False

                if not changed:
                    stats['unchanged'] += 1
                    continue

                action = 'created' if created else 'updated'
                if created:
                    stats['created'] += 1
                else:
                    stats['updated'] += 1
                stats['items_detail'].append({
                    'title': defaults['title'],
                    'slug': slug,
                    'action': action,
                    'content_type': 'interview_question',
                })

            except Exception as e:
                stats['errors'].append({'file': rel_path, 'error': str(e)})
                logger.warning(
                    'Error syncing interview question %s: %s', rel_path, e,
                )

    # Delete stale categories from this repo
    stale = InterviewCategory.objects.filter(
        source_repo=source.repo_name,
    ).exclude(slug__in=seen_slugs)
    for cat in stale:
        stats['items_detail'].append({
            'title': cat.title,
            'slug': cat.slug,
            'action': 'deleted',
            'content_type': 'interview_question',
        })
    deleted_count = stale.count()
    stale.delete()
    stats['deleted'] = deleted_count

    return stats


# ===========================================================================
# Workshop sync (issue #295)
# ===========================================================================
#
# Workshops live under ``YYYY/YYYY-MM-DD-slug/`` folders in the public
# ``AI-Shipping-Labs/workshops-content`` repo. Each folder may contain a
# ``workshop.yaml`` describing the workshop plus a series of numbered
# ``NN-name.md`` pages. Folders without ``workshop.yaml`` are code-only and
# silently skipped.
#
# The pipeline mirrors ``_sync_courses`` in shape (single-entry helper +
# stale cleanup) but is flatter — there is no module layer between a
# Workshop and its WorkshopPage rows.


def _coerce_workshop_date(value):
    """Parse a workshop ``date:`` frontmatter value into a ``datetime.date``.

    Accepts strings (ISO format) and ``datetime.date`` / ``datetime.datetime``
    values (PyYAML often parses an ISO date directly into a ``date``).
    Returns ``None`` on empty input so the caller can decide whether to fall
    back to the folder-name date prefix.
    """
    import datetime as dt

    if value in (None, ''):
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str):
        return dt.date.fromisoformat(value.strip())
    raise ValueError(f'Unsupported workshop date value: {value!r}')


def _extract_workshop_folder_date(folder_name):
    """Pull a ``YYYY-MM-DD`` prefix out of a workshop folder name if present.

    Folder names follow the ``YYYY-MM-DD-slug`` convention. Returns a
    ``datetime.date`` on a match or ``None`` otherwise. Used as a fallback
    when ``workshop.yaml`` omits the ``date:`` key.
    """
    import datetime as dt

    match = re.match(r'^(\d{4})-(\d{2})-(\d{2})', folder_name)
    if not match:
        return None
    try:
        return dt.date(
            int(match.group(1)), int(match.group(2)), int(match.group(3)),
        )
    except ValueError:
        return None


def _sync_workshops(source, repo_dir, commit_sha, sync_log, known_images=None):
    """Sync workshops from a ``workshops-content``-shaped repo.

    Layouts supported:

    - Flat (preferred): ``<repo_dir>/YYYY-MM-DD-slug/workshop.yaml``
    - Nested (legacy): ``<repo_dir>/YYYY/YYYY-MM-DD-slug/workshop.yaml``

    At the repo root we accept any dir whose name starts with
    ``YYYY-MM-DD-`` as a candidate workshop folder. Numeric-year dirs
    (``YYYY``) are still descended into for backward compat with the
    old nested layout. Folders without ``workshop.yaml`` are silently
    skipped (code-only). Missing required frontmatter is logged per-file
    and the rest of the sync continues.

    Stale workshops (folder deleted between syncs) are set to ``status='draft'``.
    The linked Event is NOT unpublished — it's standalone and may have been
    edited independently in Studio.
    """
    from content.models import Workshop

    stats = {
        'created': 0, 'updated': 0, 'unchanged': 0, 'deleted': 0,
        'errors': [], 'items_detail': [],
    }
    seen_slugs = set()
    failed_slugs = set()

    # Collect candidate workshop dirs in two passes so the flat-root and
    # nested-YYYY layouts can coexist.
    candidate_dirs = []

    for entry in sorted(os.scandir(repo_dir), key=lambda e: e.name):
        if not entry.is_dir() or entry.name.startswith('.'):
            continue
        # Numeric-year directory — descend and pick up any child folder
        # that contains a workshop.yaml (backward compat with nested
        # ``YYYY/YYYY-MM-DD-slug/`` layout).
        if re.fullmatch(r'\d{4}', entry.name):
            for child in sorted(os.scandir(entry.path), key=lambda e: e.name):
                if not child.is_dir() or child.name.startswith('.'):
                    continue
                if os.path.isfile(os.path.join(child.path, 'workshop.yaml')):
                    candidate_dirs.append(child.path)
            continue
        # Flat-root layout: any ``YYYY-MM-DD-<slug>`` dir at the root
        # that contains a workshop.yaml is a workshop folder.
        if re.match(r'^\d{4}-\d{2}-\d{2}-', entry.name):
            if os.path.isfile(os.path.join(entry.path, 'workshop.yaml')):
                candidate_dirs.append(entry.path)
            continue
        # Legacy test shim: non-dated top-level dir that directly contains
        # a workshop.yaml is still treated as a workshop folder.
        if os.path.isfile(os.path.join(entry.path, 'workshop.yaml')):
            candidate_dirs.append(entry.path)

    # Dedup while preserving scan order.
    seen_candidates = []
    for d in candidate_dirs:
        if d not in seen_candidates:
            seen_candidates.append(d)

    for workshop_path in seen_candidates:
        _sync_single_workshop(
            workshop_path, repo_dir, source, commit_sha, stats,
            seen_slugs, failed_slugs, known_images=known_images,
        )

    # Stale cleanup: workshops whose source folder disappeared this sync.
    # Set status to 'draft' (same pattern as _sync_courses). The linked
    # Event is intentionally left alone — it stands on its own.
    stale = Workshop.objects.filter(
        source_repo=source.repo_name,
        status='published',
    ).exclude(slug__in=seen_slugs).exclude(slug__in=failed_slugs)
    for ws in stale:
        stats['items_detail'].append({
            'title': ws.title,
            'slug': ws.slug,
            'action': 'deleted',
            'content_type': 'workshop',
        })
    deleted_count = stale.count()
    stale.update(status='draft')
    stats['deleted'] += deleted_count

    return stats


def _sync_single_workshop(
    workshop_dir, repo_dir, source, commit_sha, stats,
    seen_slugs, failed_slugs, known_images=None,
):
    """Parse one ``workshop.yaml`` folder into a ``Workshop`` with pages.

    Mirrors ``_sync_single_course`` but without a module layer. Validates
    frontmatter (including the split-gate rule) before writing anything and
    logs per-file errors to ``stats['errors']`` rather than aborting the sync.
    """
    # Deferred imports: the integrations service is loaded by the Django
    # AppConfig chain, and importing content/events models at module-top
    # would tie those apps' import timing to this one. Matches the pattern
    # used by every other ``_sync_*`` helper in this file.
    from content.access import VISIBILITY_CHOICES
    from content.models import Workshop

    yaml_path = os.path.join(workshop_dir, 'workshop.yaml')
    data = None
    try:
        data = _parse_yaml_file(yaml_path)
        rel_path = os.path.relpath(workshop_dir, repo_dir)
        yaml_rel_path = os.path.relpath(yaml_path, repo_dir)

        # Required frontmatter: content_id, slug, title, pages_required_level.
        _validate_frontmatter(data, 'workshop', yaml_rel_path)

        workshop_content_id = data.get('content_id')
        slug = data.get('slug')
        title = data.get('title')
        pages_required_level = data.get('pages_required_level')

        # Validate pages_required_level is a legal visibility tier level.
        valid_levels = {level for level, _ in VISIBILITY_CHOICES}
        if pages_required_level not in valid_levels:
            raise ValueError(
                f'Invalid pages_required_level={pages_required_level!r}; '
                f'must be one of {sorted(valid_levels)}'
            )

        # Recording block (optional). When present and ``url`` is set,
        # ``required_level`` must be set AND must be >= pages_required_level.
        # Fails closed — missing or too-low gate means no workshop row.
        recording = data.get('recording') or {}
        if not isinstance(recording, dict):
            raise ValueError(
                f'recording must be a mapping/dict, got {type(recording).__name__}'
            )
        recording_url = recording.get('url', '') or ''
        if recording_url:
            recording_required_level = recording.get('required_level')
            if recording_required_level is None:
                raise ValueError(
                    'recording.url is set but recording.required_level is '
                    'missing — refusing to leak the recording under the '
                    'pages_required_level gate.'
                )
            if recording_required_level not in valid_levels:
                raise ValueError(
                    f'Invalid recording.required_level='
                    f'{recording_required_level!r}; must be one of '
                    f'{sorted(valid_levels)}'
                )
            if recording_required_level < pages_required_level:
                raise ValueError(
                    f'recording.required_level ({recording_required_level}) '
                    f'must be >= pages_required_level ({pages_required_level}).'
                )
        else:
            # No recording URL — default the gate to pages_required_level
            # so the model invariant holds. When the recording is added
            # later, the author must update the yaml with a proper level.
            recording_required_level = pages_required_level

        # Slug collision check across sources.
        if _check_slug_collision(Workshop, slug, source.repo_name, rel_path):
            stats['errors'].append({
                'file': yaml_rel_path,
                'error': (
                    f"Slug collision: '{slug}' already exists from a "
                    f"different source. Skipped."
                ),
            })
            failed_slugs.add(slug)
            return

        seen_slugs.add(slug)

        # Workshop date: prefer ``date:`` frontmatter, fall back to the
        # ``YYYY-MM-DD`` prefix on the folder name.
        workshop_date = _coerce_workshop_date(data.get('date'))
        if workshop_date is None:
            workshop_date = _extract_workshop_folder_date(
                os.path.basename(workshop_dir.rstrip(os.sep)),
            )
        if workshop_date is None:
            raise ValueError(
                'workshop.yaml is missing a `date:` and the folder name '
                "doesn't start with YYYY-MM-DD — can't infer workshop date."
            )

        # Cover image — rewrite relative paths to CDN URLs like course.yaml.
        cover_image_url = rewrite_cover_image_url(
            data.get('cover_image', '') or data.get('cover_image_url', ''),
            source, yaml_rel_path,
        )

        workshop_defaults = {
            'title': title,
            'description': data.get('description', '') or '',
            'date': workshop_date,
            'instructor_name': data.get('instructor_name', '') or '',
            'tags': data.get('tags', []) or [],
            'cover_image_url': cover_image_url,
            'status': 'published',
            'pages_required_level': pages_required_level,
            'recording_required_level': recording_required_level,
            'code_repo_url': data.get('code_repo_url', '') or '',
            'source_repo': source.repo_name,
            'source_path': rel_path,
            'source_commit': commit_sha,
            'content_id': workshop_content_id,
        }

        # Find by content_id (stable) first, then slug (backward compat).
        workshop = Workshop.objects.filter(
            content_id=workshop_content_id,
            source_repo=source.repo_name,
        ).first()
        if workshop is None:
            workshop = Workshop.objects.filter(
                slug=slug,
                source_repo=source.repo_name,
            ).first()

        if workshop is None:
            workshop = Workshop(slug=slug, **workshop_defaults)
            workshop.save()
            created = True
            changed = True
        else:
            identity_changed = (
                workshop.slug != slug
                or workshop.source_path != rel_path
            )
            if identity_changed or _defaults_differ(workshop, workshop_defaults):
                workshop.slug = slug
                for k, v in workshop_defaults.items():
                    setattr(workshop, k, v)
                workshop.save()
                created = False
                changed = True
            else:
                created = False
                changed = False

        if changed:
            action = 'created' if created else 'updated'
            if created:
                stats['created'] += 1
            else:
                stats['updated'] += 1
            stats['items_detail'].append({
                'title': title,
                'slug': slug,
                'action': action,
                'content_type': 'workshop',
            })
        else:
            stats['unchanged'] += 1

        # Link or create the Event. Shared slug — ``/events/<slug>`` and
        # ``/workshops/<slug>`` live under different prefixes.
        _link_or_create_workshop_event(
            workshop, data, recording, recording_required_level,
            workshop_date, source, rel_path, yaml_rel_path, commit_sha, stats,
        )

        # Sync pages — every *.md file in the folder except README.md.
        _sync_workshop_pages(
            workshop, workshop_dir, repo_dir, source.repo_name,
            commit_sha, stats, known_images=known_images,
        )

    except Exception as e:
        try:
            failed_slug = (data or {}).get(
                'slug', os.path.basename(workshop_dir.rstrip(os.sep)),
            )
        except Exception:
            failed_slug = os.path.basename(workshop_dir.rstrip(os.sep))
        failed_slugs.add(failed_slug)
        stats['errors'].append({
            'file': os.path.relpath(yaml_path, repo_dir),
            'error': str(e),
        })
        logger.warning(
            'Error syncing workshop %s: %s',
            os.path.basename(workshop_dir.rstrip(os.sep)), e,
        )


def _link_or_create_workshop_event(
    workshop, data, recording, recording_required_level, workshop_date,
    source, rel_path, yaml_rel_path, commit_sha, stats,
):
    """Attach a matching ``Event`` to ``workshop``, creating one if missing.

    Idempotency: if an Event already exists with ``slug == workshop.slug``
    we link to it and update *content* fields only (recording metadata,
    title, description, tags, etc.). Operational fields — ``start_datetime``,
    ``end_datetime``, ``status``, ``zoom_*`` — are intentionally left
    alone. Running the sync a second time never creates a second Event.

    The Event carries its own ``content_id`` separate from the Workshop's
    (they're different models). We mint a stable UUIDv5 keyed by
    ``(repo, source_path)`` so re-syncs pick up the same event row.
    """
    import datetime as dt

    # Deferred imports: same rationale as _sync_single_workshop — content
    # and events models are loaded lazily to keep the integrations app
    # importable independently of the content app's readiness.
    from content.models import Workshop
    from events.models import Event

    # Content fields we always update from the workshop.yaml
    tags = data.get('tags', []) or []
    materials = recording.get('materials', []) or []
    timestamps = recording.get('timestamps', []) or []
    recording_url = recording.get('url', '') or ''
    recording_embed_url = recording.get('embed_url', '') or ''

    content_defaults = {
        'title': workshop.title,
        'description': workshop.description,
        'tags': tags,
        'cover_image_url': workshop.cover_image_url,
        'recording_url': recording_url,
        'recording_embed_url': recording_embed_url,
        'timestamps': timestamps,
        'materials': materials,
        'required_level': recording_required_level,
        'speaker_name': workshop.instructor_name,
        'kind': 'workshop',
        'content_id': _derive_workshop_event_content_id(
            source.repo_name, rel_path,
        ),
        'source_repo': source.repo_name,
        'source_path': yaml_rel_path,
        'source_commit': commit_sha,
    }

    # Look up by slug first — that's the idempotent key per the spec.
    event = Event.objects.filter(slug=workshop.slug).first()
    if event is None:
        start_dt = dt.datetime.combine(
            workshop_date, dt.time.min, tzinfo=dt.timezone.utc,
        )
        event = Event(
            slug=workshop.slug,
            start_datetime=start_dt,
            event_type='async',
            status='completed',
            published=True,
            **content_defaults,
        )
        event.save()
        stats['items_detail'].append({
            'title': workshop.title,
            'slug': workshop.slug,
            'action': 'created',
            'content_type': 'event',
        })
        stats['created'] += 1
    else:
        # Existing Event: update *content* fields only. Operational fields
        # (start_datetime, status, zoom_*, event_type, published) are not
        # in content_defaults so they're never touched.
        if _defaults_differ(event, content_defaults):
            for k, v in content_defaults.items():
                setattr(event, k, v)
            event.save()
            stats['items_detail'].append({
                'title': event.title,
                'slug': event.slug,
                'action': 'updated',
                'content_type': 'event',
            })
            stats['updated'] += 1
        else:
            stats['unchanged'] += 1

    # Link the Workshop to the Event if not already linked (or if linked
    # to a stale event row). Use update() to avoid re-running Workshop.save()
    # and the render pipeline.
    if workshop.event_id != event.pk:
        Workshop.objects.filter(pk=workshop.pk).update(event=event)
        workshop.event_id = event.pk


def _derive_workshop_event_content_id(repo_name, workshop_source_path):
    """Stable UUIDv5 for the Event row linked to a workshop.

    Deliberately distinct from the Workshop's ``content_id`` — Event and
    Workshop are different models with different stable IDs. Keyed on
    ``(repo_name, source_path)`` so re-syncs reuse the same Event row.
    """
    key = f'{repo_name}:{workshop_source_path}:workshop_event'
    return str(uuid.uuid5(uuid.NAMESPACE_URL, key))


def _sync_workshop_pages(
    workshop, workshop_dir, repo_dir, repo_name, commit_sha, stats,
    known_images=None,
):
    """Sync ``*.md`` pages under a workshop folder into ``WorkshopPage`` rows.

    Filename convention: ``NN-slug.md`` where ``NN`` is the sort order
    (e.g. ``01-overview.md`` -> ``sort_order=1, slug='overview'``).
    Frontmatter ``slug`` / ``sort_order`` override the filename-derived
    values. ``README.md`` is excluded (workshops don't surface READMEs
    in the page list — they'd clash with the auto-derived index page).

    Pages whose source files disappeared are hard-deleted — unlike
    course units, WorkshopPages don't carry user progress yet, so a
    soft-delete layer would be dead weight.
    """
    from content.models import WorkshopPage

    seen_paths = set()

    for filename in sorted(os.listdir(workshop_dir)):
        if (
            not filename.endswith('.md')
            or filename.upper() == 'README.MD'
            or filename.startswith('.')
        ):
            continue

        filepath = os.path.join(workshop_dir, filename)
        if not os.path.isfile(filepath):
            continue
        rel_path = os.path.relpath(filepath, repo_dir)

        try:
            metadata, body = _parse_markdown_file(filepath)

            # Required field: title (body without a title would render poorly).
            _validate_frontmatter(metadata, 'workshop_page', rel_path)

            # Derive slug / sort_order from filename unless overridden.
            sort_order = metadata.get(
                'sort_order', extract_sort_order(filename),
            )
            slug = metadata.get('slug', derive_slug(filename))

            # content_id: explicit in frontmatter, or derive stable UUID.
            content_id = metadata.get('content_id')
            if not content_id:
                content_id = _derive_workshop_page_content_id(
                    repo_name, rel_path,
                )

            seen_paths.add(rel_path)

            # Rewrite relative image URLs and flag broken references so the
            # sync report surfaces them (same pattern as articles/units).
            base_dir = os.path.dirname(rel_path)
            if known_images is not None:
                _check_broken_image_refs(
                    body, rel_path, repo_name, base_dir,
                    known_images, stats['errors'],
                )
            body = rewrite_image_urls(body, repo_name, base_dir)

            defaults = {
                'title': metadata['title'],
                'slug': slug,
                'sort_order': sort_order,
                'body': body,
                'source_path': rel_path,
                'source_commit': commit_sha,
                'content_id': content_id,
            }

            try:
                page = WorkshopPage.objects.get(
                    workshop=workshop, source_path=rel_path,
                )
            except WorkshopPage.DoesNotExist:
                page = WorkshopPage(workshop=workshop, **defaults)
                page.save()
                created = True
                changed = True
            else:
                if _defaults_differ(page, defaults):
                    for k, v in defaults.items():
                        setattr(page, k, v)
                    page.save()
                    created = False
                    changed = True
                else:
                    created = False
                    changed = False

            if not changed:
                stats['unchanged'] += 1
                continue

            action = 'created' if created else 'updated'
            if created:
                stats['created'] += 1
            else:
                stats['updated'] += 1
            stats['items_detail'].append({
                'title': page.title,
                'slug': page.slug,
                'action': action,
                'content_type': 'workshop_page',
            })

        except Exception as e:
            stats['errors'].append({'file': rel_path, 'error': str(e)})
            logger.warning('Error syncing workshop page %s: %s', rel_path, e)

    # Hard-delete pages whose source files disappeared.
    stale = WorkshopPage.objects.filter(workshop=workshop).exclude(
        source_path__in=seen_paths,
    )
    deleted_count = stale.count()
    if deleted_count:
        for p in stale:
            stats['items_detail'].append({
                'title': p.title,
                'slug': p.slug,
                'action': 'deleted',
                'content_type': 'workshop_page',
            })
        stale.delete()
        stats['deleted'] += deleted_count
