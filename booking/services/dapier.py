"""Dapier integration client for retrieving Google Calendar access tokens."""

import json
import logging
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from integrations.config import get_config

logger = logging.getLogger(__name__)

CALENDAR_CONNECTION_ID = "google-calendar"

# In-memory access token cache
_token_cache: dict[str, tuple[str, float]] = {}


class DapierError(Exception):
    pass


class DapierNotConfigured(DapierError):
    pass


class DapierUnavailable(DapierError):
    pass


def get_dapier_config() -> tuple[str, str, str]:
    """Retrieve base_url, api_token, and agent_name."""
    base_url = get_config("DAPIER_BASE_URL", "https://dapier.datatalks.club").rstrip("/")
    api_token = get_config("DAPIER_API_TOKEN", "")
    agent = get_config("DAPIER_AGENT_NAME", "ai-shipping-labs")
    return base_url, api_token, agent


def get_calendar_access_token(*, force_refresh: bool = False, timeout: int = 10) -> str:
    """Obtain a Google Calendar Bearer access token via Dapier agent API.

    POST {base_url}/api/agent/token
    Body: {"connection_id": "google-calendar", "agent": agent}
    Header: Authorization: Bearer {dapier_api_token}

    Returns:
        access_token string
    """
    now = time.time()
    cached = _token_cache.get(CALENDAR_CONNECTION_ID)
    if cached and not force_refresh:
        token, expires_at = cached
        if expires_at > now + 60:
            return token

    base_url, api_token, agent = get_dapier_config()
    if not base_url or not agent:
        raise DapierNotConfigured("Dapier base URL and agent name must be configured.")

    if not api_token:
        # If no token is set in DB or env, raise DapierNotConfigured
        raise DapierNotConfigured("DAPIER_API_TOKEN is not configured.")

    url = f"{base_url}/api/agent/token"
    payload = json.dumps(
        {
            "connection_id": CALENDAR_CONNECTION_ID,
            "agent": agent,
        }
    ).encode("utf-8")

    req = Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_token}",
        },
    )

    try:
        with urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        logger.warning("Dapier token request failed with HTTP %s", exc.code)
        raise DapierUnavailable(f"Dapier refused token request: HTTP {exc.code}") from exc
    except (URLError, TimeoutError) as exc:
        logger.warning("Dapier unreachable: %s", exc)
        raise DapierUnavailable(f"Dapier unreachable: {exc}") from exc

    access_token = data.get("access_token")
    if not access_token:
        raise DapierError("Dapier response did not contain access_token")

    expires_in = int(data.get("expires_in", 3600))
    _token_cache[CALENDAR_CONNECTION_ID] = (access_token, now + expires_in)
    return access_token


def clear_dapier_token_cache():
    """Clear cached token (useful for tests)."""
    _token_cache.clear()
