"""Small JSON/HTTP clients for the release helper."""

import json
import urllib.error
import urllib.request

from .errors import ReleaseError


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject redirects so credentials and verification stay on their origin."""

    def redirect_request(self, request, fp, code, message, headers, new_url):
        return None


def no_redirect_opener():
    return urllib.request.build_opener(NoRedirectHandler())


class JsonClient:
    """Authenticated JSON client with explicit scheme and redacted failures."""

    def __init__(self, base_url, token, auth_scheme="Bearer", timeout=40, opener=None):
        if auth_scheme not in {"Bearer", "Token"}:
            raise ReleaseError("unsupported-api-auth-scheme")
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.auth_scheme = auth_scheme
        self.timeout = timeout
        self.opener = opener or no_redirect_opener()

    def call(self, method, path, payload=None):
        data = None
        if payload is not None:
            data = json.dumps(payload).encode()
        request = urllib.request.Request(
            self.base_url + path,
            data=data,
            method=method,
            headers=self._headers(),
        )
        return self._open(request, method, path)

    def _headers(self):
        return {
            "Authorization": f"{self.auth_scheme} {self.token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _open(self, request, method, path):
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                body = response.read()
        except urllib.error.HTTPError as error:
            raise ReleaseError(
                f"api-http-error method={method} path={path} status={error.code}"
            ) from None
        except (urllib.error.URLError, TimeoutError):
            raise ReleaseError(
                f"api-transport-error method={method} path={path}"
            ) from None
        if not body:
            return {}
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            raise ReleaseError(
                f"api-invalid-json method={method} path={path}"
            ) from None


def get_text(url, timeout=20, opener=None):
    """Read a public text endpoint without following redirects."""
    request = urllib.request.Request(url, headers={"Accept": "text/plain"})
    active_opener = opener or no_redirect_opener()
    try:
        with active_opener.open(request, timeout=timeout) as response:
            return response.read().decode().strip()
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
        raise ReleaseError("public-ping-request-failed") from None
