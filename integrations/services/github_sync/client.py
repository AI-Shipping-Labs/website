"""GitHub API client helpers for content sync."""

import hashlib
import hmac
import time

import jwt
import requests
from django.core.cache import cache

from integrations.config import get_config
from integrations.models import ContentSource
from integrations.services.github_sync.common import (
    GITHUB_API_BASE,
    INSTALLATION_REPOS_CACHE_KEY,
    INSTALLATION_REPOS_CACHE_TIMEOUT,
    GitHubSyncError,
    logger,
)


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
        repo_full_name: Full repo name (e.g. "AI-Shipping-Labs/content").

    Returns:
        ContentSource or None. ``repo_name`` is unique, so at most one row
        matches.
    """
    return ContentSource.objects.filter(repo_name=repo_full_name).first()


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
