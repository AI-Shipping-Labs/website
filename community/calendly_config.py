"""Calendly configuration helpers (issue #884, Phase 2).

All Calendly settings are runtime-configurable via the IntegrationSetting
framework, so they are editable from Studio settings with no redeploy.
Never read raw ``os.environ`` / ``settings.X`` for these values; go
through :func:`integrations.config.get_config` / ``is_enabled`` here so
the DB override > env > default resolution and the Source badge work.
"""

from integrations.config import get_config, is_enabled

CALENDLY_OAUTH_AUTHORIZE_URL = 'https://auth.calendly.com/oauth/authorize'
CALENDLY_OAUTH_TOKEN_URL = 'https://auth.calendly.com/oauth/token'


def get_calendly_access_token():
    """Host access token used for Calendly API calls. '' when unset."""
    return get_config('CALENDLY_ACCESS_TOKEN', '')


def get_calendly_webhook_signing_key():
    """Signing key for verifying webhook callbacks. '' when unset."""
    return get_config('CALENDLY_WEBHOOK_SIGNING_KEY', '')


def get_calendly_oauth_client_id():
    """Calendly OAuth app client ID. '' when unset."""
    return get_config('CALENDLY_OAUTH_CLIENT_ID', '')


def get_calendly_oauth_client_secret():
    """Calendly OAuth app client secret. '' when unset."""
    return get_config('CALENDLY_OAUTH_CLIENT_SECRET', '')


def calendly_webhook_validation_enabled():
    """True when webhook signatures must be verified (recommended in prod)."""
    return is_enabled('CALENDLY_WEBHOOK_VALIDATION_ENABLED')
