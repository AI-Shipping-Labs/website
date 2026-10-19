"""Configuration: token + base-URL resolution (staff/admin scope only).

Resolution order for the staff API token:
  1. ``ASL_API_TOKEN`` environment variable.
  2. ``API_SHIPPING_LABS_API_TOKEN`` key in the repo ``.env`` file.
  3. The device-login credentials file (``~/.config/asl/credentials.json``,
     written by ``asl login`` with mode 0600).
  4. Interactive prompt (TTY only).

Device login is the preferred way to sign in: run ``asl login``, approve
in the browser, and the CLI stores the resulting staff token. Existing
``ASL_API_TOKEN`` / ``.env`` tokens keep working. To refresh a stored
credential, run ``asl login`` again; ``asl logout`` removes the file
(the server-side token itself is revoked in Studio).

Base URL: ``ASL_BASE_URL`` env var, default ``https://aishippinglabs.com``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from community_base.device_login.storage import (
    read_private_json,
    save_private_json,
)

DEFAULT_BASE_URL = "https://aishippinglabs.com"

_STAFF_ENV_KEYS = ("ASL_API_TOKEN", "API_SHIPPING_LABS_API_TOKEN")


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
    stored = read_stored_credential(resolve_base_url())
    if stored:
        return stored
    return _prompt("Staff API token")


def resolve_base_url() -> str:
    """Return the configured base URL (no trailing slash)."""
    return os.environ.get("ASL_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def credentials_path() -> Path:
    """Location of the device-login credentials file (0600)."""
    return Path(
        os.environ.get("ASL_CREDENTIALS_PATH", "~/.config/asl/credentials.json")
    ).expanduser()


def read_stored_credential(base_url: str) -> str | None:
    """Return the stored token when the file targets ``base_url``."""
    path = credentials_path()
    if not path.is_file():
        return None
    try:
        profile = read_private_json(path)
    except PermissionError as error:
        raise RuntimeError(
            f"Stored credentials at {path} must have mode 0600. "
            "Fix the permissions or run `asl logout` and `asl login` again."
        ) from error
    except (OSError, ValueError) as error:
        raise RuntimeError(f"Cannot read stored credentials at {path}.") from error
    if not isinstance(profile, dict):
        raise RuntimeError(f"Cannot read stored credentials at {path}.")
    if profile.get("base_url") != base_url:
        return None
    token = profile.get("token", "")
    return token.strip() or None


def write_stored_credential(*, base_url: str, token: str, email: str = "") -> Path:
    """Atomically store the device-login credential with mode 0600."""
    path = credentials_path()
    return save_private_json(path, {"base_url": base_url, "token": token, "email": email})


def clear_stored_credential() -> bool:
    """Remove the stored credential file. Returns True when one existed."""
    path = credentials_path()
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True


def _prompt(label: str) -> str:
    if not sys.stdin.isatty():
        raise RuntimeError(
            f"{label} not found in env or .env, and stdin is not a TTY. "
            "Set ASL_API_TOKEN in your environment."
        )
    import getpass

    return getpass.getpass(f"{label}: ").strip()
