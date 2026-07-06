"""Thin HTTP client over the AI Shipping Labs API.

Wraps ``httpx`` with token auth, slashless paths, JSON decode, and
structured error raising. All command modules go through this client.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx

from asl_cli.config import resolve_base_url, resolve_staff_token


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
            headers={"Authorization": f"Token {self._token}"},
            timeout=30.0,
            follow_redirects=True,
        )

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any | None = None,
        raw: bool = False,
    ) -> Any:
        """Send a request and return decoded JSON (or raw text if ``raw``)."""
        # No trailing slashes — the site middleware 301s them.
        if path != "/" and path.endswith("/"):
            path = path.rstrip("/")

        response = self._http.request(method, path, params=params, json=json_body)

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
