"""Calendly OAuth connect / callback (issue #884, Phase 2).

Optional authorize-Calendly flow: instead of pasting a personal access
token into Studio settings, a staff member can click "Connect Calendly",
authorize the platform's Calendly OAuth app, and have the resulting host
access token stored as the ``CALENDLY_ACCESS_TOKEN`` IntegrationSetting.

The access token is a runtime-configurable secret and therefore lives in
``IntegrationSetting`` (same row a manual paste would write), so the rest
of the integration reads it through ``get_calendly_access_token()`` with
zero special-casing. The live round-trip against the real Calendly
account is verified manually ([HUMAN] criteria on #884).
"""

import base64
import hashlib
import hmac
import logging
import secrets
from urllib.parse import urlencode

import requests
from django.contrib import messages
from django.shortcuts import redirect
from django.views.decorators.http import require_POST

from community.calendly_config import (
    CALENDLY_OAUTH_AUTHORIZE_URL,
    CALENDLY_OAUTH_TOKEN_URL,
    get_calendly_oauth_client_id,
    get_calendly_oauth_client_secret,
)
from integrations.config import site_base_url
from integrations.services.calendly_api import (
    REQUIRED_SCOPES,
    CalendlyAPIError,
    store_token_response,
    validate_connection_and_ensure_subscription,
)
from studio.decorators import staff_required

logger = logging.getLogger(__name__)

CALLBACK_PATH = '/studio/integrations/calendly/callback'
OAUTH_TIMEOUT_SECONDS = 15
SESSION_STATE_KEY = 'calendly_oauth_state'
SESSION_VERIFIER_KEY = 'calendly_oauth_code_verifier'


def _redirect_uri():
    """Absolute callback URL registered with the Calendly OAuth app."""
    return f'{site_base_url().rstrip("/")}{CALLBACK_PATH}'


@staff_required
def calendly_connect(request):
    """Kick off the Calendly OAuth authorize redirect.

    Requires the OAuth client ID to be configured; otherwise sends the
    staff member back to settings with a helpful message.
    """
    client_id = get_calendly_oauth_client_id()
    if not client_id:
        messages.error(
            request,
            'Set the Calendly OAuth client ID in settings before connecting.',
        )
        return redirect('studio_settings')

    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest(),
    ).rstrip(b'=').decode()
    request.session[SESSION_STATE_KEY] = state
    request.session[SESSION_VERIFIER_KEY] = verifier
    params = {
        'client_id': client_id,
        'response_type': 'code',
        'redirect_uri': _redirect_uri(),
        'state': state,
        'scope': ' '.join(REQUIRED_SCOPES),
        'code_challenge_method': 'S256',
        'code_challenge': challenge,
    }
    return redirect(f'{CALENDLY_OAUTH_AUTHORIZE_URL}?{urlencode(params)}')


@staff_required
def calendly_callback(request):
    """Exchange the OAuth code for a host access token and store it.

    Best-effort: any failure (denied authorization, missing creds, token
    exchange error) routes back to settings with a message rather than
    raising. On success the access token is written to the same
    ``CALENDLY_ACCESS_TOKEN`` IntegrationSetting a manual paste uses.
    """
    expected_state = request.session.pop(SESSION_STATE_KEY, '')
    verifier = request.session.pop(SESSION_VERIFIER_KEY, '')
    presented_state = request.GET.get('state', '')
    if not expected_state or not verifier or not hmac.compare_digest(
        expected_state, presented_state,
    ):
        messages.error(request, 'Calendly authorization session expired or was invalid.')
        return redirect('studio_settings')

    error = request.GET.get('error')
    if error:
        messages.error(request, f'Calendly authorization failed: {error}')
        return redirect('studio_settings')

    code = request.GET.get('code')
    if not code:
        messages.error(request, 'Calendly did not return an authorization code.')
        return redirect('studio_settings')

    client_id = get_calendly_oauth_client_id()
    client_secret = get_calendly_oauth_client_secret()
    if not client_id or not client_secret:
        messages.error(
            request,
            'Calendly OAuth client ID and secret must both be set to connect.',
        )
        return redirect('studio_settings')

    try:
        response = requests.post(
            CALENDLY_OAUTH_TOKEN_URL,
            data={
                'grant_type': 'authorization_code',
                'code': code,
                'redirect_uri': _redirect_uri(),
                'client_id': client_id,
                'client_secret': client_secret,
                'code_verifier': verifier,
            },
            timeout=OAUTH_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        token_data = response.json() or {}
    except (requests.RequestException, ValueError):
        logger.exception('Calendly OAuth token exchange failed')
        messages.error(request, 'Could not exchange the Calendly authorization code.')
        return redirect('studio_settings')

    if not token_data.get('access_token'):
        messages.error(request, 'Calendly did not return an access token.')
        return redirect('studio_settings')

    try:
        store_token_response(token_data, require_new_refresh_token=True)
        validate_connection_and_ensure_subscription()
    except CalendlyAPIError as exc:
        logger.exception('Calendly connection validation/subscription failed')
        messages.error(request, f'Could not complete Calendly setup: {exc}')
        return redirect('studio_settings')
    messages.success(request, 'Connected Calendly and verified the webhook subscription.')
    return redirect('studio_settings')


@staff_required
@require_POST
def calendly_sync(request):
    """Operator-safe idempotent subscription validation/provisioning action."""
    try:
        validate_connection_and_ensure_subscription()
    except CalendlyAPIError as exc:
        messages.error(request, f'Calendly synchronization failed: {exc}')
    else:
        messages.success(request, 'Calendly connection and webhook subscription verified.')
    return redirect('studio_settings')
