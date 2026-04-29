"""Repository checkout, HEAD lookup, and path helper utilities."""

import os
import re
import subprocess
import uuid
from pathlib import PurePath

from django.conf import settings

from integrations.services.github_sync.client import generate_github_app_token
from integrations.services.github_sync.common import GitHubSyncError, logger


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

