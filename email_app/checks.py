"""Django system checks for Relay outbound email configuration."""

from __future__ import annotations

from urllib.parse import urlsplit

from django.conf import settings
from django.core.checks import Error, Tags, register

from integrations.config import get_config


def _configuration_error(message, hint):
    return Error(message, hint=hint, id="email_app.E002")


def _relay_credentials_errors():
    errors = []
    base_url = (get_config("RELAY_BASE_URL", "") or "").strip()
    api_key = (get_config("RELAY_API_KEY", "") or "").strip()
    parsed = urlsplit(base_url)
    if not base_url or parsed.scheme not in {"http", "https"} or not parsed.netloc:
        errors.append(_configuration_error(
            "RELAY_BASE_URL must be a valid absolute HTTP(S) URL.",
            "Configure RELAY_BASE_URL in environment or Studio settings.",
        ))
    if not api_key:
        errors.append(_configuration_error(
            "RELAY_API_KEY must be configured.",
            "Configure the AISL client Relay API key in environment or Studio settings.",
        ))
    return errors


@register(Tags.security)
def check_relay_configuration_in_production(app_configs, **kwargs):
    """Reject production deployments without working Relay configuration."""
    if settings.TESTING or settings.DEBUG:
        return []
    errors = _relay_credentials_errors()
    environment = (get_config("OBSERVABILITY_ENVIRONMENT", "") or "").lower()
    is_prod = environment in {"production", "prod"}
    is_prod = is_prod or "aishippinglabs.com" in settings.ALLOWED_HOSTS
    if is_prod and bool(get_config("RELAY_TRANSACTIONAL_DRY_RUN", False)):
        errors.append(Error(
            "RELAY_TRANSACTIONAL_DRY_RUN is enabled in production! Real transactional mail will not be sent.",
            hint="Disable RELAY_TRANSACTIONAL_DRY_RUN in production.",
            id="email_app.E003",
        ))
    return errors


@register(Tags.compatibility)
def check_email_backend_selection(app_configs, **kwargs):
    """Only explicit offline tests may choose a Django backend other than Relay."""
    if settings.TESTING:
        return []
    if settings.EMAIL_BACKEND == "email_app.backends.relay.RelayEmailBackend":
        return []
    return [Error(
        "Django EMAIL_BACKEND must route through Relay.",
        hint="Set EMAIL_BACKEND = 'email_app.backends.relay.RelayEmailBackend'.",
        id="email_app.E004",
    )]


@register(Tags.security)
def check_package_mail_backend_relay(app_configs, **kwargs):
    """Only explicit offline tests may choose a package backend other than Relay."""
    if settings.TESTING:
        return []
    if settings.COMMUNITY_BASE.get("MAIL_BACKEND") == "relay":
        return []
    return [Error(
        "COMMUNITY_BASE['MAIL_BACKEND'] must route through Relay.",
        hint="Set COMMUNITY_BASE_MAIL_BACKEND=relay in the deploy environment.",
        id="email_app.E005",
    )]
