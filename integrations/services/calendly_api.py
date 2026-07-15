"""Calendly OAuth token lifecycle and API/subscription client (#884)."""

from datetime import timedelta

import requests
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from community.calendly_config import (
    CALENDLY_OAUTH_TOKEN_URL,
    get_calendly_access_token,
    get_calendly_access_token_expires_at,
    get_calendly_oauth_client_id,
    get_calendly_oauth_client_secret,
    get_calendly_organization_uri,
    get_calendly_refresh_token,
)
from integrations.config import clear_config_cache, site_base_url
from integrations.models import IntegrationSetting

API_BASE = 'https://api.calendly.com'
WEBHOOK_CALLBACK_PATH = '/api/webhooks/calendly'
REQUIRED_SCOPES = ('scheduled_events:read', 'webhooks:write')
TOKEN_KEYS = {
    'access_token': ('CALENDLY_ACCESS_TOKEN', True),
    'refresh_token': ('CALENDLY_REFRESH_TOKEN', True),
    'expires_at': ('CALENDLY_ACCESS_TOKEN_EXPIRES_AT', False),
}


class CalendlyAPIError(RuntimeError):
    def __init__(self, message, *, status_code=None):
        self.status_code = status_code
        super().__init__(message)


def _upsert_setting(key, value, *, secret=False):
    IntegrationSetting.objects.update_or_create(
        key=key,
        defaults={
            'value': str(value or ''), 'is_secret': secret, 'group': 'calendly',
            'description': f'Calendly managed value for {key}.',
        },
    )


@transaction.atomic
def store_token_response(data, *, require_new_refresh_token=False):
    """Atomically store an OAuth token response.

    The initial authorization-code exchange must include a refresh token so
    the integration cannot report a short-lived, non-refreshable connection.
    During a later refresh Calendly may omit ``refresh_token``; in that case
    the already-locked durable token remains authoritative.
    """
    access_token = str(data.get('access_token') or '').strip()
    if not access_token:
        raise CalendlyAPIError('Calendly token response omitted access_token')
    # Lock all existing token rows so two refreshes cannot reuse one token.
    token_rows = list(IntegrationSetting.objects.select_for_update().filter(
        key__in=[value[0] for value in TOKEN_KEYS.values()],
    ))
    existing_refresh_token = next(
        (
            row.value for row in token_rows
            if row.key == 'CALENDLY_REFRESH_TOKEN'
        ),
        '',
    )
    returned_refresh_token = str(data.get('refresh_token') or '').strip()
    if require_new_refresh_token and not returned_refresh_token:
        raise CalendlyAPIError('Calendly token response omitted refresh_token')
    refresh_token = returned_refresh_token or existing_refresh_token
    if not refresh_token:
        raise CalendlyAPIError('Calendly token response omitted refresh_token')
    try:
        expires_in = max(0, int(data.get('expires_in', 7200)))
    except (TypeError, ValueError) as exc:
        raise CalendlyAPIError('Calendly token response had invalid expires_in') from exc
    expires_at = timezone.now() + timedelta(seconds=expires_in)
    values = {
        'access_token': access_token,
        'refresh_token': refresh_token,
        'expires_at': expires_at.isoformat(),
    }
    for name, value in values.items():
        key, secret = TOKEN_KEYS[name]
        _upsert_setting(key, value, secret=secret)
    clear_config_cache()
    return values


def clear_oauth_tokens():
    IntegrationSetting.objects.filter(
        key__in=[value[0] for value in TOKEN_KEYS.values()],
    ).delete()
    clear_config_cache()


def _token_expiring():
    raw = get_calendly_access_token_expires_at()
    if not raw:
        return False  # A manually configured personal access token.
    expires_at = parse_datetime(raw)
    return expires_at is None or expires_at <= timezone.now() + timedelta(minutes=2)


def refresh_access_token():
    # Serialize refresh-token use across workers. Calendly refresh tokens are
    # single-use, so reading/calling/rotating must be one locked critical section.
    failure_status = None
    with transaction.atomic():
        IntegrationSetting.objects.select_for_update().filter(
            key='CALENDLY_ACCESS_TOKEN',
        ).first()
        clear_config_cache()
        refresh_token = get_calendly_refresh_token()
        if not refresh_token:
            raise CalendlyAPIError('Calendly access token expired; reconnect Calendly')
        try:
            response = requests.post(
                CALENDLY_OAUTH_TOKEN_URL,
                data={
                    'grant_type': 'refresh_token',
                    'refresh_token': refresh_token,
                    'client_id': get_calendly_oauth_client_id(),
                    'client_secret': get_calendly_oauth_client_secret(),
                },
                timeout=15,
            )
        except requests.RequestException as exc:
            raise CalendlyAPIError('Calendly token refresh request failed') from exc
        if response.status_code not in (200, 201):
            failure_status = response.status_code
            if failure_status in (400, 401):
                clear_oauth_tokens()
        else:
            try:
                data = response.json()
            except ValueError as exc:
                raise CalendlyAPIError('Calendly token refresh returned invalid JSON') from exc
            store_token_response(data)
    if failure_status is not None:
        raise CalendlyAPIError('Calendly token refresh failed', status_code=failure_status)
    return get_calendly_access_token()


def valid_access_token():
    token = get_calendly_access_token()
    if not token:
        raise CalendlyAPIError('Calendly access token is not configured')
    return refresh_access_token() if _token_expiring() else token


def api_request(method, path, *, params=None, json=None):
    token = valid_access_token()
    response = _request(method, path, token=token, params=params, json=json)
    if response.status_code == 401 and get_calendly_refresh_token():
        token = refresh_access_token()
        response = _request(method, path, token=token, params=params, json=json)
    if not 200 <= response.status_code < 300:
        raise CalendlyAPIError(
            f'Calendly API {method} {path} failed', status_code=response.status_code,
        )
    try:
        return response.json() if response.content else {}
    except ValueError as exc:
        raise CalendlyAPIError(f'Calendly API {method} {path} returned invalid JSON') from exc


def _request(method, path, *, token, params=None, json=None):
    try:
        return requests.request(
            method, f'{API_BASE}{path}', params=params, json=json,
            headers={'Authorization': f'Bearer {token}'}, timeout=15,
        )
    except requests.RequestException as exc:
        raise CalendlyAPIError(f'Calendly API {method} {path} request failed') from exc


def validate_connection_and_ensure_subscription():
    """Validate identity and idempotently provision the required webhook."""
    me = api_request('GET', '/users/me').get('resource') or {}
    organization = me.get('current_organization') or get_calendly_organization_uri()
    if not me.get('uri') or not organization:
        raise CalendlyAPIError('Calendly current-user response omitted identity/organization')
    callback_url = f'{site_base_url().rstrip("/")}{WEBHOOK_CALLBACK_PATH}'
    base_params = {'organization': organization, 'scope': 'organization'}
    page_token = ''
    seen_page_tokens = set()
    subscriptions = []
    while True:
        params = dict(base_params)
        if page_token:
            params['page_token'] = page_token
        page = api_request('GET', '/webhook_subscriptions', params=params)
        subscriptions.extend(page.get('collection') or [])
        next_page_token = str(
            (page.get('pagination') or {}).get('next_page_token') or '',
        ).strip()
        if not next_page_token:
            break
        if next_page_token in seen_page_tokens:
            raise CalendlyAPIError(
                'Calendly webhook subscription pagination repeated a page token',
            )
        seen_page_tokens.add(next_page_token)
        page_token = next_page_token
    required_events = {'invitee.created', 'invitee.canceled'}
    existing = next(
        (item for item in subscriptions if item.get('callback_url') == callback_url
         and item.get('state', 'active') == 'active'
         and required_events.issubset(set(item.get('events') or []))),
        None,
    )
    if existing is None:
        created = api_request('POST', '/webhook_subscriptions', json={
            'url': callback_url,
            'events': sorted(required_events),
            'organization': organization,
            'scope': 'organization',
        }).get('resource') or {}
        subscription_uri = created.get('uri', '')
    else:
        subscription_uri = existing.get('uri', '')
    with transaction.atomic():
        _upsert_setting('CALENDLY_CONNECTED_USER_URI', me['uri'])
        _upsert_setting('CALENDLY_ORGANIZATION_URI', organization)
        _upsert_setting('CALENDLY_WEBHOOK_SUBSCRIPTION_URI', subscription_uri)
    clear_config_cache()
    return {'user_uri': me['uri'], 'organization_uri': organization,
            'subscription_uri': subscription_uri}
