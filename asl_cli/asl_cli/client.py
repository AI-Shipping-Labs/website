"""Thin HTTP client over the AI Shipping Labs API.

Wraps ``httpx`` with token auth, slashless paths, JSON decode, and
structured error raising. All command modules go through this client.
"""

from __future__ import annotations

import json
import random
import sys
import time
from dataclasses import dataclass
from typing import Any

import httpx

from asl_cli.config import resolve_base_url, resolve_staff_token

# Issue #1854: the server sheds per-token load with a 429 carrying one of
# these codes and a ``Retry-After`` header. Those rejections happen before
# the endpoint runs, so every method is safe to retry. Other 429s (e.g.
# ``too_many_users`` from tier reconcile) are real answers and never retried.
THROTTLE_CODES = frozenset({"rate_limited", "too_many_concurrent_requests"})
MAX_THROTTLE_RETRIES = 5
MAX_BACKOFF_SECONDS = 30.0


def _throttle_code(response: httpx.Response) -> str | None:
    if response.status_code != 429:
        return None
    try:
        body = response.json()
    except ValueError:
        return None
    if not isinstance(body, dict) or body.get("code") not in THROTTLE_CODES:
        return None
    return body["code"]


def _retry_after_seconds(response: httpx.Response) -> float:
    try:
        return max(0.0, float(response.headers.get("Retry-After", "0")))
    except ValueError:
        return 0.0


def throttle_backoff_seconds(response: httpx.Response, attempt: int) -> float:
    """Wait for ``Retry-After`` or exponential backoff, plus jitter, capped."""
    exponential = 2.0 ** attempt
    delay = min(MAX_BACKOFF_SECONDS, max(_retry_after_seconds(response), exponential))
    jittered = delay + random.uniform(0, delay / 4)
    return min(MAX_BACKOFF_SECONDS, jittered)


@dataclass
class APIError(Exception):
    """Raised when the server returns a non-2xx status."""

    status: int
    body: Any
    url: str

    def __str__(self) -> str:
        if isinstance(self.body, dict) and "error" in self.body:
            code = self.body.get("code", "")
            suffix = f" [{code}]" if code else ""
            return f"HTTP {self.status}{suffix}: {self.body['error']}"
        return f"HTTP {self.status}: {self.body}"


class Client:
    """Authenticated staff HTTP client targeting ``/api``."""

    def __init__(self, *, base_url: str | None = None):
        self.base_url = base_url or resolve_base_url()
        self._token = resolve_staff_token()
        self._http = httpx.Client(
            base_url=self.base_url,
            timeout=30.0,
            follow_redirects=True,
        )
        self._sleep = time.sleep

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any | None = None,
        headers: dict[str, str] | None = None,
        raw: bool = False,
    ) -> Any:
        """Send a request and return decoded JSON (or raw text if ``raw``)."""
        # No trailing slashes — the site middleware 301s them.
        if path != "/" and path.endswith("/"):
            path = path.rstrip("/")

        request_headers = {"Authorization": f"Token {self._token}"}
        # community-base operator endpoints use bearer API keys. Keep the
        # legacy Token header for the site's existing /api compatibility
        # routes until those routes migrate in their owning issues.
        if path.lstrip("/").startswith("api/v1/"):
            request_headers["Authorization"] = f"Bearer {self._token}"
        if headers:
            request_headers.update(headers)
        response = self._send_with_backoff(
            method, path, params=params, json=json_body, headers=request_headers,
        )

        if response.status_code >= 400:
            try:
                error_body = response.json()
            except Exception:
                error_body = response.text
            raise APIError(response.status_code, error_body, str(response.url))

        if raw:
            return response.text
        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except json.JSONDecodeError:
            return response.text

    def _send_with_backoff(self, method: str, path: str, **kwargs) -> httpx.Response:
        """Send once, retrying throttled 429s at most ``MAX_THROTTLE_RETRIES`` times."""
        response = self._http.request(method, path, **kwargs)
        for attempt in range(MAX_THROTTLE_RETRIES):
            code = _throttle_code(response)
            if code is None:
                return response
            delay = throttle_backoff_seconds(response, attempt)
            print(
                f"asl: throttled ({code}); retrying in {delay:.1f}s",
                file=sys.stderr,
            )
            self._sleep(delay)
            response = self._http.request(method, path, **kwargs)
        return response

    def get(self, path: str, **kwargs) -> Any:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, *, json_body: Any | None = None, **kwargs) -> Any:
        return self.request("POST", path, json_body=json_body, **kwargs)

    def patch(self, path: str, *, json_body: Any | None = None, **kwargs) -> Any:
        return self.request("PATCH", path, json_body=json_body, **kwargs)

    def put(self, path: str, *, json_body: Any | None = None, **kwargs) -> Any:
        return self.request("PUT", path, json_body=json_body, **kwargs)

    def delete(self, path: str, **kwargs) -> Any:
        return self.request("DELETE", path, **kwargs)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "Client":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()


def staff_client() -> Client:
    """Convenience factory for the staff API client."""
    return Client()
