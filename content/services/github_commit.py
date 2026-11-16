"""Resolve the commit a learner's GitHub project link points at."""

import logging
import re
from urllib.parse import urlparse

import requests

from integrations.config import get_config

logger = logging.getLogger(__name__)

GITHUB_API_BASE = 'https://api.github.com'
REQUEST_TIMEOUT_SECONDS = 5

NOT_GITHUB_MESSAGE = 'Enter a link to a GitHub repository, for example https://github.com/owner/repo.'
NOT_FOUND_MESSAGE = 'Make sure the repository exists and is public.'
UNAVAILABLE_MESSAGE = 'Could not reach GitHub. Try again in a few minutes.'

_SHA_RE = re.compile(r'^[0-9a-fA-F]{7,40}$')
_NAME_RE = re.compile(r'^[A-Za-z0-9._-]+$')


class GitHubCommitError(Exception):
    """Base class; ``message`` is safe to show to the learner."""

    def __init__(self, message):
        super().__init__(message)
        self.message = message


class InvalidRepositoryUrl(GitHubCommitError):
    """The link is not a github.com repository URL."""


class RepositoryNotFound(GitHubCommitError):
    """The repository or ref does not exist or is not public."""


class GitHubUnavailable(GitHubCommitError):
    """Timeout, network error, 5xx or rate limit: retry later."""


def parse_github_url(url):
    """Return ``(owner, repo, ref)``; ``ref`` is ``'HEAD'`` for a plain link."""
    parsed = urlparse((url or '').strip())
    if parsed.scheme not in ('http', 'https'):
        raise InvalidRepositoryUrl(NOT_GITHUB_MESSAGE)
    host = (parsed.hostname or '').lower()
    if host not in ('github.com', 'www.github.com'):
        raise InvalidRepositoryUrl(NOT_GITHUB_MESSAGE)
    parts = [part for part in parsed.path.split('/') if part]
    if len(parts) < 2:
        raise InvalidRepositoryUrl(NOT_GITHUB_MESSAGE)
    owner, repo = parts[0], parts[1]
    if repo.endswith('.git'):
        repo = repo[:-4]
    if not _NAME_RE.match(owner) or not _NAME_RE.match(repo) or not repo:
        raise InvalidRepositoryUrl(NOT_GITHUB_MESSAGE)
    ref = 'HEAD'
    if len(parts) >= 4 and parts[2] == 'tree':
        ref = '/'.join(parts[3:])
    elif len(parts) >= 4 and parts[2] in ('blob', 'commit', 'commits'):
        ref = parts[3]
    return owner, repo, ref


def github_commit_url(repo_url, commit_id):
    """``https://github.com/<owner>/<repo>/commit/<sha>`` or '' when unparseable."""
    if not commit_id:
        return ''
    try:
        owner, repo, _ref = parse_github_url(repo_url)
    except InvalidRepositoryUrl:
        return ''
    return f'https://github.com/{owner}/{repo}/commit/{commit_id}'


_token_cache = {'token': '', 'expires': 0.0}


def _auth_headers():
    """Authorization header from the GitHub App, or empty (anonymous)."""
    import time

    if not get_config('GITHUB_APP_ID'):
        return {}
    if _token_cache['token'] and _token_cache['expires'] > time.time():
        return {'Authorization': f"Bearer {_token_cache['token']}"}
    try:
        from integrations.services.github_app import generate_github_app_token

        token = generate_github_app_token()
    except Exception:  # noqa: BLE001 - never block a submission on auth
        logger.warning('GitHub App token unavailable; using anonymous request')
        return {}
    _token_cache.update(token=token, expires=time.time() + 50 * 60)
    return {'Authorization': f'Bearer {token}'}


def resolve_commit(github_link):
    """The full commit SHA the link points at."""
    owner, repo, ref = parse_github_url(github_link)
    headers = {
        'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
        **_auth_headers(),
    }
    try:
        response = requests.get(
            f'{GITHUB_API_BASE}/repos/{owner}/{repo}/commits/{ref}',
            headers=headers,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        logger.warning('GitHub commit lookup failed: %s', exc)
        raise GitHubUnavailable(UNAVAILABLE_MESSAGE) from exc

    status = response.status_code
    if status == 429 or (
        status == 403 and response.headers.get('X-RateLimit-Remaining') == '0'
    ):
        raise GitHubUnavailable(UNAVAILABLE_MESSAGE)
    if status >= 500:
        raise GitHubUnavailable(UNAVAILABLE_MESSAGE)
    if status != 200:
        raise RepositoryNotFound(NOT_FOUND_MESSAGE)
    try:
        sha = response.json().get('sha', '')
    except ValueError as exc:
        raise GitHubUnavailable(UNAVAILABLE_MESSAGE) from exc
    if not _SHA_RE.match(sha or ''):
        raise GitHubUnavailable(UNAVAILABLE_MESSAGE)
    return sha.lower()
