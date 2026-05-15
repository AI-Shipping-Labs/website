"""Public helper for OAuth provider context on registration surfaces.

Issue #652 promoted ``_oauth_provider_context`` out of
``accounts/views/auth.py`` so any view that needs to render the inline
register card (course detail, workshop pages-level paywall, pricing
page) can populate the same ``oauth_*_enabled`` flags the standalone
register/login pages already use.

Provider buttons are gated on a ``SocialApp`` row existing with a
non-empty ``client_id`` — operators clear credentials in Studio to
disable a provider without deleting the row.
"""

from allauth.socialaccount.models import SocialApp


def get_oauth_provider_context():
    """Return the OAuth provider flags used by every register surface.

    Returns:
        dict with ``oauth_google_enabled``, ``oauth_github_enabled``,
        ``oauth_slack_enabled`` (each boolean).
    """
    configured_providers = set(
        SocialApp.objects.exclude(client_id='').values_list(
            'provider', flat=True,
        )
    )
    return {
        'oauth_google_enabled': 'google' in configured_providers,
        'oauth_github_enabled': 'github' in configured_providers,
        'oauth_slack_enabled': 'slack' in configured_providers,
    }
