"""Device-login loop for ``asl login`` (community_base.device_login).

Mirrors the shared ``community_base.device_login.client`` prompt and poll
semantics: request a device code, print the verification URL plus the user
code (the device code is never printed), and poll until the browser
approves, denies, or the code expires.
"""

from __future__ import annotations

from typing import Any, Callable
from urllib.parse import urlsplit

from community_base.device_login.client import (
    DeviceLoginError,
)
from community_base.device_login.client import (
    default_label as cb_default_label,
)
from community_base.device_login.client import (
    device_login as cb_device_login,
)

__all__ = ["DeviceLoginError", "default_label", "device_login"]


def default_label() -> str:
    return cb_default_label("asl CLI")


class _ClientAdapter:
    def __init__(self, client: Any):
        self.client = client

    def post(self, url: str, json: Any = None, timeout: float = 30.0) -> Any:
        parsed = urlsplit(url)
        path = parsed.path
        try:
            return self.client.post(path, json=json)
        except (TypeError, ValueError):
            return self.client.post(url, json=json)


def device_login(
    base_url: str,
    *,
    label: str | None = None,
    timeout: float = 600,
    printer: Callable[[str], None] | None = None,
    client: Any | None = None,
) -> dict[str, Any]:
    session = _ClientAdapter(client) if client is not None else None
    return cb_device_login(
        base_url,
        label=label or default_label(),
        timeout=timeout,
        printer=printer,
        session=session,
    )

