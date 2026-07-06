"""Configuration: token + base-URL resolution.

Resolution order for the staff API token:
  1. ``ASL_API_TOKEN`` environment variable.
  2. ``API_SHIPPING_LABS_API_TOKEN`` key in the repo ``.env`` file
     (searched upward from the cwd).
  3. Interactive prompt (only in a TTY).

The member API key resolves analogously from ``ASL_MEMBER_API_KEY``
then ``AI_SHIPPING_LABS_MEMBER_API_KEY``.

Base URL: ``ASL_BASE_URL`` env var, default ``https://aishippinglabs.com``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

DEFAULT_BASE_URL = "https://aishippinglabs.com"

_STAFF_ENV_KEYS = ("ASL_API_TOKEN", "API_SHIPPING_LABS_API_TOKEN")
_MEMBER_ENV_KEYS = ("ASL_MEMBER_API_KEY", "AI_SHIPPING_LABS_MEMBER_API_KEY")


def _find_env_file() -> Path | None:
    """Search upward from cwd for a ``.env`` file."""
    cwd = Path.cwd()
    for directory in [cwd, *cwd.parents]:
        candidate = directory / ".env"
        if candidate.is_file():
            return candidate
    return None


def _read_env_value(keys: tuple[str, ...]) -> str | None:
    """Return the first non-empty value from env vars, then ``.env``."""
    for key in keys:
        value = os.environ.get(key, "").strip()
        if value:
            return value

    env_file = _find_env_file()
    if env_file is None:
        return None
    try:
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            env_key, _, raw_value = line.partition("=")
            env_key = env_key.strip()
            if env_key in keys:
                value = raw_value.strip().strip('"').strip("'")
                if value:
                    return value
    except OSError:
        pass
    return None


def resolve_staff_token() -> str:
    """Resolve the staff API token or prompt for it."""
    token = _read_env_value(_STAFF_ENV_KEYS)
    if token:
        return token
    return _prompt("Staff API token")


def resolve_member_token() -> str:
    """Resolve the member API key or prompt for it."""
    token = _read_env_value(_MEMBER_ENV_KEYS)
    if token:
        return token
    return _prompt("Member API key")


def resolve_base_url() -> str:
    """Return the configured base URL (no trailing slash)."""
    return os.environ.get("ASL_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def _prompt(label: str) -> str:
    if not sys.stdin.isatty():
        raise RuntimeError(
            f"{label} not found in env or .env, and stdin is not a TTY. "
            "Set ASL_API_TOKEN (or ASL_MEMBER_API_KEY) in your environment."
        )
    import getpass

    return getpass.getpass(f"{label}: ").strip()
