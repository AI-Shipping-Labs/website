"""Relay availability and configuration health check (mirroring CMP 061e8607 / AC 5)."""

from __future__ import annotations

import logging
import os
from typing import Any
from urllib.parse import urlsplit

import requests
from django.conf import settings

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


def get_relay_health_status() -> dict[str, Any]:
    """Return health summary for Relay delivery."""
    is_valid, error = validate_relay_config()
    dry_run = bool(get_config("RELAY_TRANSACTIONAL_DRY_RUN", False))
    dry_run_prod = is_production() and dry_run

    is_healthy = is_valid and not dry_run_prod
    return {
        "status": "ok" if is_healthy else "FAIL",
        "degraded": not is_healthy,
        "relay_status": "ok" if is_healthy else "FAIL",
        "error": "dry_run_on_prod" if dry_run_prod else (error or ""),
        "dry_run": dry_run,
        "dry_run_prod": dry_run_prod,
    }
