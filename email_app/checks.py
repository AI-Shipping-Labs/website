"""Django system checks for the ``email_app`` (Relay cutover P4).

The SES_ENABLED E001 production gate is gone with the SES send paths:
every send goes through Relay, and a production-like environment without
Relay credentials fails the E002 check below instead.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from django.conf import settings
from django.core.checks import Error, Tags, register

from integrations.config import get_config


@register(Tags.security)
def check_relay_configuration_in_production(app_configs, **kwargs):
    """Refuse a production-like deploy when Relay is enabled but misconfigured (AC 5)."""
    if getattr(settings, "TESTING", False):
        return []
    if getattr(settings, "DEBUG", False):
        return []

    community_base = getattr(settings, "COMMUNITY_BASE", {})
    if community_base.get("MAIL_BACKEND") != "relay":
        return []

    base_url = (get_config("RELAY_BASE_URL", getattr(settings, "RELAY_BASE_URL", "")) or "").strip()
    api_key = (get_config("RELAY_API_KEY", getattr(settings, "RELAY_API_KEY", "")) or "").strip()

    errors = []

    if not base_url:
        errors.append(
            Error(
                "COMMUNITY_BASE['MAIL_BACKEND']='relay' but RELAY_BASE_URL is not configured.",
                hint="Set RELAY_BASE_URL (e.g. https://relay.datatalks.club) in environment or Studio settings.",
                id="email_app.E002",
            )
        )
    else:
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            errors.append(
                Error(
                    f"RELAY_BASE_URL '{base_url}' is not a valid absolute HTTP(S) URL.",
                    hint="Configure a valid URL like https://relay.datatalks.club.",
                    id="email_app.E002",
                )
            )

    if not api_key:
        errors.append(
            Error(
                "COMMUNITY_BASE['MAIL_BACKEND']='relay' but RELAY_API_KEY is not configured.",
                hint="Set the AISL client Relay API key in environment or Studio settings.",
                id="email_app.E002",
            )
        )

    # Dry-run guard on production
    dry_run = bool(get_config("RELAY_TRANSACTIONAL_DRY_RUN", False))
    env_name = (
        get_config("OBSERVABILITY_ENVIRONMENT", "")
        or getattr(settings, "ENVIRONMENT", "")
    ).lower()
    is_prod = env_name in {"production", "prod"} or "aishippinglabs.com" in getattr(settings, "ALLOWED_HOSTS", [])
    if is_prod and dry_run:
        errors.append(
            Error(
                "RELAY_TRANSACTIONAL_DRY_RUN is enabled in production! Real transactional mail will not be sent.",
                hint="Disable RELAY_TRANSACTIONAL_DRY_RUN in production.",
                id="email_app.E003",
            )
        )

    return errors


@register(Tags.compatibility)
def check_email_backend_selection(app_configs, **kwargs):
    """Ensure Django core mail EMAIL_BACKEND is Relay-backed and never SMTP/SES default (AC 3)."""
    if getattr(settings, "TESTING", False):
        return []

    backend = getattr(settings, "EMAIL_BACKEND", "")
    if not backend or backend in {
        "django.core.mail.backends.smtp.EmailBackend",
        "django_ses.SESBackend",
    }:
        return [
            Error(
                f"EMAIL_BACKEND is set to '{backend or '<unset>'}'. Django core mail must route through Relay.",
                hint="Set EMAIL_BACKEND = 'email_app.backends.relay.RelayEmailBackend'.",
                id="email_app.E004",
            )
        ]
    return []
