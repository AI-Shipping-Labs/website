"""Helpers for managing OAuth login providers shown in Studio settings.

Reads and writes ``allauth.socialaccount.models.SocialApp`` rows for the
three providers we expose on the login page (Google, GitHub, Slack). The
Studio Auth & Login section is the operator UI on top of this storage —
``django-allauth`` itself reads ``SocialApp`` directly when handling the
``/accounts/{provider}/login/`` redirect.

Kept deliberately small: the operator only ever sees binary
Configured / Not configured status, so we do not model "partial" here.
"""

from allauth.socialaccount.models import SocialApp

# Whitelist of providers exposed in Studio. Keep in sync with
# ``SOCIALACCOUNT_PROVIDERS`` in ``website/settings.py`` and the buttons on
# ``templates/accounts/login.html``.
SUPPORTED_PROVIDERS = ('google', 'github', 'slack')

# Static metadata per provider — display labels, the developer-console URL
# the operator must visit, and the callback path allauth registers. The
# scopes are read from ``SOCIALACCOUNT_PROVIDERS`` at runtime so the card
# never drifts from what allauth actually requests.
PROVIDER_META = {
    'google': {
        'label': 'Google OAuth',
        'name': 'Google',
        'configure_url': 'https://console.cloud.google.com/apis/credentials',
        'callback_path': '/accounts/google/login/callback/',
    },
    'github': {
        'label': 'GitHub OAuth',
        'name': 'GitHub',
        'configure_url': 'https://github.com/settings/developers',
        'callback_path': '/accounts/github/login/callback/',
    },
    'slack': {
        'label': 'Slack OAuth',
        'name': 'Slack',
        'configure_url': 'https://api.slack.com/apps',
        'callback_path': '/accounts/slack/login/callback/',
    },
}


def is_supported_provider(provider):
    """Return True if ``provider`` is one we expose in Studio settings."""
    return provider in SUPPORTED_PROVIDERS


def _get_scopes(provider, socialaccount_providers):
    """Pull the SCOPE list out of SOCIALACCOUNT_PROVIDERS for ``provider``.

    Falls back to an empty list if the provider has no scope config — we
    never want a missing key to crash settings rendering.
    """
    cfg = socialaccount_providers.get(provider) or {}
    return list(cfg.get('SCOPE') or [])


def get_auth_provider_data(provider, site_base_url, socialaccount_providers):
    """Build the dict the auth-card template needs for one provider.

    Args:
        provider: One of ``SUPPORTED_PROVIDERS``.
        site_base_url: ``settings.SITE_BASE_URL`` — used to render the
            absolute callback URL the operator pastes into the provider
            console.
        socialaccount_providers: ``settings.SOCIALACCOUNT_PROVIDERS`` —
            scope source of truth.

    Returns:
        dict with provider metadata, current credentials, and status. Safe
        to pass straight into ``_auth_card.html`` via ``include``.
    """
    meta = PROVIDER_META[provider]
    app = SocialApp.objects.filter(provider=provider).first()

    client_id = app.client_id if app else ''
    secret = app.secret if app else ''
    is_configured = bool(client_id) and bool(secret)

    return {
        'provider': provider,
        'label': meta['label'],
        'name': meta['name'],
        'configure_url': meta['configure_url'],
        'callback_url': f"{site_base_url.rstrip('/')}{meta['callback_path']}",
        'scopes': _get_scopes(provider, socialaccount_providers),
        'client_id': client_id,
        'secret': secret,
        'is_configured': is_configured,
    }


def get_all_auth_providers(site_base_url, socialaccount_providers):
    """Return the auth-card context list, in the order shown on the page."""
    return [
        get_auth_provider_data(p, site_base_url, socialaccount_providers)
        for p in SUPPORTED_PROVIDERS
    ]


def save_auth_provider(provider, client_id, secret, site):
    """Upsert the ``SocialApp`` row for ``provider`` and attach ``site``.

    Submitting empty values is intentional — that is how an operator
    disables a provider without deleting the row (see issue #322 section F).
    The login template already gates the "Sign in with X" button on the
    presence of a non-empty ``client_id``.

    Args:
        provider: One of ``SUPPORTED_PROVIDERS`` (caller MUST validate
            via ``is_supported_provider`` before calling).
        client_id: New value for ``SocialApp.client_id``.
        secret: New value for ``SocialApp.secret``.
        site: ``Site`` instance to attach via the ``sites`` m2m. Required
            by allauth's lookup; without a matching site row the
            ``/accounts/{provider}/login/`` redirect 500s.

    Returns:
        The ``SocialApp`` instance after upsert.
    """
    meta = PROVIDER_META[provider]
    app, _created = SocialApp.objects.update_or_create(
        provider=provider,
        defaults={
            'name': meta['name'],
            'client_id': client_id,
            'secret': secret,
        },
    )
    app.sites.add(site)
    return app
