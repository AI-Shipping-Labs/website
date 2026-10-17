"""Device-login loop for ``asl login`` (httpx-based).

Mirrors the shared ``community_base.device_login.client`` prompt and poll
semantics: request a device code, print the verification URL plus the user
code (the device code is never printed), and poll until the browser
approves, denies, or the code expires.
"""

from __future__ import annotations

import getpass
import socket
import time
from typing import Any, Callable

import httpx


class DeviceLoginError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def default_label() -> str:
    user = getpass.getuser() if hasattr(getpass, "getuser") else ""
    host = socket.gethostname().split(".")[0]
    label = f"{user}@{host}".strip("@")
    return label[:80] or "asl CLI"


def device_login(
    base_url: str,
    *,
    label: str | None = None,
    timeout: float = 600,
    printer: Callable[[str], None] | None = None,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    base = base_url.rstrip("/")
    http = client or httpx.Client(base_url=base, timeout=30.0, follow_redirects=True)
    say = printer or print
    try:
        response = http.post("/api/auth/device", json={"label": label or default_label()})
    except httpx.HTTPError as error:
        raise DeviceLoginError("invalid_request", f"Cannot reach {base}: {error}") from error
    if response.status_code != 200:
        raise DeviceLoginError("invalid_response", f"Login start failed: {response.text[:200]}")
    try:
        offer = response.json()
    except ValueError as error:
        raise DeviceLoginError("invalid_response", "Login start returned invalid JSON.") from error
    if not isinstance(offer, dict):
        raise DeviceLoginError("invalid_response", "Login start returned an invalid offer.")
    for field in ("device_code", "user_code", "verification_uri"):
        if not offer.get(field):
            raise DeviceLoginError("invalid_response", "Login start returned an invalid offer.")
    device_code = offer["device_code"]
    user_code = offer["user_code"]
    url = offer.get("verification_uri_complete") or offer["verification_uri"]
    expires_in = offer.get("expires_in", 600) or 600
    interval = max(float(offer.get("interval", 5) or 5), 1.0)
    minutes = max(int(round(expires_in / 60)), 1)

    say("")
    say(f"  Confirm this code:  {user_code}")
    say(f"    Open in a browser:  {url}")
    say("    Open that link on a machine you trust and sign in as usual.")
    say(f"    Waiting for confirmation (the code is valid for {minutes} minute(s))...")
    say("")

    deadline = time.monotonic() + max(timeout, expires_in + 15)
    wait = interval
    while True:
        if time.monotonic() >= deadline:
            raise DeviceLoginError("timeout", "Timed out waiting for browser approval.")
        time.sleep(wait)
        try:
            poll = http.post(
                "/api/auth/device/token",
                json={
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "device_code": device_code,
                },
            )
        except httpx.HTTPError:
            continue
        if poll.status_code == 200:
            try:
                credential = poll.json()
            except ValueError as error:
                raise DeviceLoginError(
                    "invalid_response", "Login completed with an invalid response."
                ) from error
            if not isinstance(credential, dict) or "token" not in credential:
                raise DeviceLoginError(
                    "invalid_response", "Login completed with an invalid response."
                )
            return credential
        try:
            error_code = poll.json().get("error", "") if poll.text else ""
        except ValueError:
            continue
        if error_code == "authorization_pending":
            continue
        if error_code == "slow_down" or poll.status_code == 429:
            wait = min(wait + 5, 60)
            continue
        if error_code == "access_denied":
            raise DeviceLoginError("access_denied", "The login was denied in the browser.")
        if error_code == "expired_token":
            raise DeviceLoginError("expired_token", "The code expired. Run login again.")
        raise DeviceLoginError("invalid_response", f"Login failed: {poll.text[:200]}")
