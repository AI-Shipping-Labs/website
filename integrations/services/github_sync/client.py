"""GitHub API client helpers for content sync."""

import hashlib
import hmac
import time

import jwt
import requests
from django.core.cache import cache

from integrations.config import get_config, running_in_worker_process
from integrations.models import ContentSource
from integrations.services.github_sync.common import (
    GITHUB_API_BASE,
    INSTALLATION_REPOS_CACHE_KEY,
    INSTALLATION_REPOS_CACHE_TIMEOUT,
    GitHubSyncError,
    logger,
)

# Cached AWS Secrets Manager lookup. ``website/settings.py`` previously
# resolved the GitHub App PEM at module-import time, which paid a
# Secrets Manager API round-trip (~1-2s) for every Django boot --
# multiplied across the four settings imports the old entrypoint did.
# We now resolve it lazily on the first call to
# ``generate_github_app_token``. Web processes cache successful lookups
# per secret id/region, while workers fetch fresh so long-running queue
# jobs do not hold stale credentials.
_DEFAULT_GITHUB_APP_PRIVATE_KEY_SECRET_ID = (
    'ai-shipping-labs/github-app-private-key'
)
_DEFAULT_GITHUB_APP_PRIVATE_KEY_SECRET_REGION = 'eu-west-1'
_secrets_manager_pem_cache = {}


def _fetch_github_app_private_key_from_secrets_manager(secret_id, region):
    """Fetch the GitHub App PEM from AWS Secrets Manager (cached).

    Returns an empty string if boto3 is unavailable, the secret is
    missing, or the call fails for any reason -- never raises. Callers
    treat an empty string as "no key configured".
    """
    cache_key = (secret_id, region)
    if not running_in_worker_process() and cache_key in _secrets_manager_pem_cache:
        return _secrets_manager_pem_cache[cache_key]
    try:
        import boto3  # noqa: PLC0415
        from botocore.exceptions import BotoCoreError, ClientError  # noqa: PLC0415
    except ImportError as e:
        logger.warning(
            'Failed to fetch secret %s: boto3/botocore not installed (%s)',
            secret_id, e,
        )
        return ''
    try:
        client = boto3.client(
            'secretsmanager',
            region_name=region,
        )
        value = client.get_secret_value(
            SecretId=secret_id,
        )['SecretString']
    except (BotoCoreError, ClientError) as e:
        logger.warning(
            'Failed to fetch secret %s: %s',
            secret_id, e,
        )
        return ''
    if value and not running_in_worker_process():
        _secrets_manager_pem_cache[cache_key] = value
    return value or ''


def _resolve_github_app_private_key():
    """Resolve the GitHub App private key.

    Lookup order:
      1. ``IntegrationSetting`` DB row (via ``get_config``), which also
         falls through to Django settings (``GITHUB_APP_PRIVATE_KEY``,
         which is itself resolved from a PEM file or env var at
         settings-import time).
      2. AWS Secrets Manager (production fallback). The secret id/path
         and region can be configured in Studio, with legacy defaults
         preserved for existing deployments.
    """
    private_key = get_config('GITHUB_APP_PRIVATE_KEY')
    if private_key:
        return private_key
    secret_id = get_config(
        'GITHUB_APP_PRIVATE_KEY_SECRET_ID',
        _DEFAULT_GITHUB_APP_PRIVATE_KEY_SECRET_ID,
    )
    region = get_config(
        'GITHUB_APP_PRIVATE_KEY_SECRET_REGION',
        _DEFAULT_GITHUB_APP_PRIVATE_KEY_SECRET_REGION,
    )
    if not secret_id:
        return ''
    return _fetch_github_app_private_key_from_secrets_manager(secret_id, region)


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


# Provider namespace for ``WebhookLog.deduplication_key`` so GitHub
# delivery ids can never collide with another service's fingerprint
# (the column is globally unique across every webhook source).
GITHUB_DELIVERY_KEY_PREFIX = 'github:delivery:'
GITHUB_BODY_KEY_PREFIX = 'github:body:'

# GitHub delivery ids are UUIDs (36 chars). We keep the raw value in the
# key when it is short and printable so operators can search a delivery
# GUID straight from the GitHub "Recent Deliveries" panel; anything else
# is hashed so the key always fits ``deduplication_key`` (128 chars) and
# can never be truncated into a collision with a different delivery.
_MAX_RAW_DELIVERY_ID_LENGTH = 80


def _is_plain_delivery_id(delivery_id):
    """True when the delivery id is short, ASCII, and free of separators."""
    if len(delivery_id) > _MAX_RAW_DELIVERY_ID_LENGTH:
        return False
    if not delivery_id.isascii():
        return False
    return all(char.isalnum() or char in '-_' for char in delivery_id)


def delivery_deduplication_key(request):
    """Build the replay key for an already-authenticated GitHub delivery.

    Call this only after the repository is known and the
    ``X-Hub-Signature-256`` HMAC has been verified: the key is a claim on
    processing, so an unauthenticated caller must never be able to reserve
    (or steal) one.

    GitHub stamps every delivery -- including its own retries and manual
    redeliveries -- with ``X-GitHub-Delivery``, so that header is the
    fingerprint whenever it is present.

    Missing/blank header policy: fall back to a digest of the verified
    signature header plus the raw body. That is deliberately not payload
    identity on its own -- the signature is ``HMAC(webhook_secret, body)``,
    so only a holder of the shared secret can produce it, and a caller
    without the secret cannot steer the key. The fallback fails safe: two
    byte-identical signed deliveries collapse into one claim (at worst we
    skip a redundant sync) rather than fanning out into duplicate syncs.
    """
    delivery_id = (request.headers.get('X-GitHub-Delivery') or '').strip()
    if delivery_id:
        if _is_plain_delivery_id(delivery_id):
            return f'{GITHUB_DELIVERY_KEY_PREFIX}{delivery_id}'
        digest = hashlib.sha256(delivery_id.encode('utf-8')).hexdigest()
        return f'{GITHUB_DELIVERY_KEY_PREFIX}sha256:{digest}'

    signature_header = request.headers.get('X-Hub-Signature-256', '')
    digest = hashlib.sha256(
        signature_header.encode('utf-8') + b'\0' + request.body,
    ).hexdigest()
    return f'{GITHUB_BODY_KEY_PREFIX}{digest}'


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
    private_key = _resolve_github_app_private_key()
    installation_id = get_config('GITHUB_APP_INSTALLATION_ID')

    if not all([app_id, private_key, installation_id]):
        raise GitHubSyncError(
            'GitHub App credentials not configured. '
            'Set GITHUB_APP_ID, GITHUB_APP_PRIVATE_KEY, '
            'and GITHUB_APP_INSTALLATION_ID, or configure '
            'GITHUB_APP_PRIVATE_KEY_SECRET_ID to fetch the private key '
            'from AWS Secrets Manager.'
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
