"""Central email kind classification and sender resolution."""

import os

from django.conf import settings

from integrations.config import get_config

EMAIL_KIND_TRANSACTIONAL = "transactional"
EMAIL_KIND_PROMOTIONAL = "promotional"

TRANSACTIONAL_FROM_KEY = "SES_TRANSACTIONAL_FROM_EMAIL"
PROMOTIONAL_FROM_KEY = "SES_PROMOTIONAL_FROM_EMAIL"
LEGACY_FROM_KEY = "SES_FROM_EMAIL"

DEFAULT_TRANSACTIONAL_FROM_EMAIL = "noreply@aishippinglabs.com"
DEFAULT_PROMOTIONAL_FROM_EMAIL = "content@aishippinglabs.com"

# community_invite is transactional: it grants access to the paid/member
# community. lead_magnet_delivery is transactional: it delivers a resource the
# recipient explicitly requested, not an unsolicited marketing campaign.
TRANSACTIONAL_EMAIL_TYPES = {
    "welcome",
    "payment_failed",
    "cancellation",
    "community_invite",
    "lead_magnet_delivery",
    "event_reminder",
    "email_verification",
    "email_verification_reminder",
    "password_reset",
    "event_registration",
    "welcome_imported",
}

PROMOTIONAL_EMAIL_TYPES = {
    "campaign",
}


class EmailClassificationError(ValueError):
    """Raised when an email type has no explicit kind."""


def classify_email_type(email_type):
    """Return ``transactional`` or ``promotional`` for a known email type."""
    if email_type in TRANSACTIONAL_EMAIL_TYPES:
        return EMAIL_KIND_TRANSACTIONAL
    if email_type in PROMOTIONAL_EMAIL_TYPES:
        return EMAIL_KIND_PROMOTIONAL
    raise EmailClassificationError(
        f"Email type {email_type!r} is not classified as transactional or promotional."
    )


def _integration_setting_has_value(key):
    try:
        from integrations.models import IntegrationSetting

        return IntegrationSetting.objects.filter(key=key).exclude(value="").exists()
    except Exception:
        return False


def _has_runtime_value(key, default=""):
    if os.environ.get(key):
        return True
    settings_value = getattr(settings, key, "")
    if settings_value and settings_value != default:
        return True
    return _integration_setting_has_value(key)


def get_sender_for_kind(email_kind):
    """Resolve the configured sender for an email kind.

    New explicit keys win. The legacy ``SES_FROM_EMAIL`` key remains a
    migration fallback only when the new key is not configured.
    """
    if email_kind == EMAIL_KIND_TRANSACTIONAL:
        key = TRANSACTIONAL_FROM_KEY
        default = DEFAULT_TRANSACTIONAL_FROM_EMAIL
    elif email_kind == EMAIL_KIND_PROMOTIONAL:
        key = PROMOTIONAL_FROM_KEY
        default = DEFAULT_PROMOTIONAL_FROM_EMAIL
    else:
        raise EmailClassificationError(f"Unknown email kind: {email_kind!r}")

    if _has_runtime_value(key, default):
        return get_config(key, default)

    if _has_runtime_value(LEGACY_FROM_KEY):
        return get_config(LEGACY_FROM_KEY, default)

    return default
