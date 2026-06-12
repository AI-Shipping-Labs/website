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

import logging
from urllib.parse import urlencode

import requests
from django.contrib import messages
from django.shortcuts import redirect

from community.calendly_config import (
    CALENDLY_OAUTH_AUTHORIZE_URL,
    CALENDLY_OAUTH_TOKEN_URL,
    get_calendly_oauth_client_id,
    get_calendly_oauth_client_secret,
)
from integrations.config import clear_config_cache, site_base_url
from integrations.models import IntegrationSetting
from studio.decorators import staff_required

logger = logging.getLogger(__name__)

CALLBACK_PATH = '/studio/integrations/calendly/callback'
OAUTH_TIMEOUT_SECONDS = 15


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

    params = {
        'client_id': client_id,
        'response_type': 'code',
        'redirect_uri': _redirect_uri(),
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
            },
            timeout=OAUTH_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        access_token = (response.json() or {}).get('access_token', '')
    except (requests.RequestException, ValueError):
        logger.exception('Calendly OAuth token exchange failed')
        messages.error(request, 'Could not exchange the Calendly authorization code.')
        return redirect('studio_settings')

    if not access_token:
        messages.error(request, 'Calendly did not return an access token.')
        return redirect('studio_settings')

    IntegrationSetting.objects.update_or_create(
        key='CALENDLY_ACCESS_TOKEN',
        defaults={
            'value': access_token,
            'is_secret': True,
            'group': 'calendly',
            'description': 'Calendly host access token (set via OAuth connect).',
        },
    )
    clear_config_cache()
    messages.success(request, 'Connected Calendly. Host access token stored.')
    return redirect('studio_settings')
