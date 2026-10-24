"""Offline direct-Relay transport for the local Playwright server."""

from __future__ import annotations

import hashlib
from threading import Lock

from requests import RequestException, Timeout


class StubRelayResponse:
    """Minimal response shape consumed by the direct Relay adapter."""

    status_code = 202

    def __init__(self, document):
        self._document = document

    def json(self):
        return self._document


class StubRelayTransactionalTransport:
    """Requests-compatible offline transport with Relay idempotency semantics."""

    Timeout = Timeout
    RequestException = RequestException

    def __init__(self):
        self.calls = []
        self._message_ids = {}
        self._lock = Lock()

    def post(self, url, *, json, headers, timeout):
        idempotency_key = json["idempotency_key"]
        with self._lock:
            replay = idempotency_key in self._message_ids
            message_id = self._message_ids.setdefault(
                idempotency_key,
                _message_id(idempotency_key),
            )
            self.calls.append(
                {
                    "url": url,
                    "json": json,
                    "headers": headers,
                    "timeout": timeout,
                },
            )
        return StubRelayResponse(
            {
                "message": {
                    "id": message_id,
                    "status": "queued",
                    "template_key": json["template_key"],
                    "template_version": 1,
                    "idempotency_key": idempotency_key,
                },
                "idempotent_replay": replay,
            },
        )


def _message_id(idempotency_key):
    digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:16]
    return f"relay-test-{digest}"


_transport = None


def _test_relay_config(key, default="", **kwargs):
    """Supply inert credentials only to the direct transactional adapter."""
    values = {
        "RELAY_BASE_URL": "https://relay.test",
        "RELAY_API_KEY": "test-relay-key",
    }
    if key in values:
        return values[key]
    from integrations.config import get_config  # noqa: PLC0415

    return get_config(key, default, **kwargs)


def install_test_relay_transactional_transport():
    """Install the offline transport only on the AISL direct adapter module."""
    global _transport
    from email_app import relay_transactional  # noqa: PLC0415

    if _transport is None:
        _transport = StubRelayTransactionalTransport()
    relay_transactional.requests = _transport
    relay_transactional.get_config = _test_relay_config
    return _transport
