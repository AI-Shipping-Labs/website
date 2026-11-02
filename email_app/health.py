"""Relay availability and configuration health check (mirroring CMP 061e8607 / AC 5)."""

from __future__ import annotations

import logging
import os
from typing import Any
from urllib.parse import urlsplit

import requests
from django.conf import settings

from email_app.relay_transactional import get_sender_fallback_flag
from email_app.services.email_classification import (
    get_calendar_fallback_sender,
    get_calendar_sender,
    get_sender_for_email_type,
    relay_sender_id,
)
from integrations.config import get_config

logger = logging.getLogger(__name__)


def is_production() -> bool:
    """Return True if running in production."""
    if getattr(settings, "DEBUG", False) or getattr(settings, "TESTING", False):
        return False
    env_name = (
        get_config("OBSERVABILITY_ENVIRONMENT", "")
        or os.environ.get("ENVIRONMENT", "")
        or ""
    ).lower()
    return env_name in {"production", "prod"} or "aishippinglabs.com" in getattr(settings, "ALLOWED_HOSTS", [])


def validate_relay_config() -> tuple[bool, str]:
    """Validate RELAY_BASE_URL and RELAY_API_KEY.

    Returns (is_valid, error_code).
    """
    base_url = (get_config("RELAY_BASE_URL", getattr(settings, "RELAY_BASE_URL", "")) or "").strip()
    api_key = (get_config("RELAY_API_KEY", getattr(settings, "RELAY_API_KEY", "")) or "").strip()

    if not base_url or not api_key:
        return False, "relay_config_missing"

    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False, "relay_config_invalid"

    return True, ""


def check_relay_availability(timeout: float = 3.0) -> tuple[bool, str]:
    """Check if Relay /health/ready returns 200.

    Returns (is_available, error_message).
    """
    valid, reason = validate_relay_config()
    if not valid:
        return False, reason

    base_url = get_config("RELAY_BASE_URL", getattr(settings, "RELAY_BASE_URL", "")).rstrip("/")
    try:
        res = requests.get(f"{base_url}/health/ready", timeout=timeout)
        if res.status_code == 200:
            return True, ""
        return False, f"relay_http_{res.status_code}"
    except requests.RequestException as exc:
        return False, f"relay_unreachable: {exc}"


def required_relay_sender_ids() -> list[str]:
    """Return every sender ID selected by the site's email classification."""
    sender_addresses = [
        get_sender_for_email_type("event_registration"),
        get_sender_for_email_type("campaign"),
        get_sender_for_email_type("welcome"),
        get_calendar_sender(),
        get_calendar_fallback_sender(),
    ]
    sender_ids = set()
    for address in sender_addresses:
        sender_id = relay_sender_id(address)
        if sender_id:
            sender_ids.add(sender_id)
    return sorted(sender_ids)


def _configured_sender_ids(payload) -> set[str] | None:
    if not isinstance(payload, dict):
        return None
    sender_emails = payload.get("sender_emails")
    if not isinstance(sender_emails, list):
        return None
    sender_ids = set()
    for sender in sender_emails:
        if isinstance(sender, dict) and isinstance(sender.get("id"), str):
            sender_ids.add(sender["id"])
    return sender_ids


def check_relay_sender_configuration(timeout: float = 3.0) -> tuple[bool, str]:
    """Verify every sender used by this site exists in the authenticated Relay client."""
    is_valid, reason = validate_relay_config()
    if not is_valid:
        return False, reason
    base_url = get_config("RELAY_BASE_URL", getattr(settings, "RELAY_BASE_URL", "")).rstrip("/")
    api_key = get_config("RELAY_API_KEY", getattr(settings, "RELAY_API_KEY", ""))
    try:
        response = requests.get(
            f"{base_url}/api/client/senders",
            headers={"Accept": "application/json", "Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )
    except requests.RequestException as exc:
        logger.warning("Relay sender preflight unavailable error_type=%s", type(exc).__name__)
        return False, "relay_sender_check_unavailable"
    if response.status_code != 200:
        return False, f"relay_sender_check_http_{response.status_code}"
    try:
        configured_ids = _configured_sender_ids(response.json())
    except (TypeError, ValueError):
        configured_ids = None
    if configured_ids is None:
        return False, "relay_sender_check_invalid_response"
    missing_ids = sorted(set(required_relay_sender_ids()) - configured_ids)
    if missing_ids:
        return False, f"relay_senders_missing:{','.join(missing_ids)}"
    return True, ""


def get_relay_health_status() -> dict[str, Any]:
    """Return health summary for Relay delivery."""
    is_valid, error = validate_relay_config()
    dry_run = bool(get_config("RELAY_TRANSACTIONAL_DRY_RUN", False))
    dry_run_prod = is_production() and dry_run

    is_healthy = is_valid and not dry_run_prod
    sender_fallback = get_sender_fallback_flag()
    return {
        "sender_fallback": sender_fallback or None,
        "warning": (
            f"relay_sender_not_configured: {sender_fallback['sender_id']} "
            f"(using {sender_fallback['fallback_sender_id']})"
            if sender_fallback else ""
        ),
        "status": "ok" if is_healthy else "FAIL",
        "degraded": not is_healthy,
        "relay_status": "ok" if is_healthy else "FAIL",
        "error": "dry_run_on_prod" if dry_run_prod else (error or ""),
        "dry_run": dry_run,
        "dry_run_prod": dry_run_prod,
    }
