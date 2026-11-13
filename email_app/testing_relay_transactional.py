"""Offline direct-Relay transport for the local Playwright server."""

from __future__ import annotations

import hashlib
import json
from threading import Lock

import requests
from requests import RequestException, Timeout


class StubRelayResponse:
    """Minimal response shape consumed by the direct Relay adapter."""

    status_code = 202

    def __init__(self, document):
        self._document = document
        self.text = json.dumps(document)
        self.content = self.text.encode("utf-8")
        self.headers = {"content-type": "application/json"}

    def json(self):
        return self._document


class StubRelayTransactionalTransport:
    """Requests-compatible offline transport with Relay idempotency semantics."""

    Timeout = Timeout
    RequestException = RequestException
    exceptions = requests.exceptions

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
                    "template_version": json.get("template_version", 1),
                    "idempotency_key": idempotency_key,
                },
                "idempotent_replay": replay,
            },
        )


class _StubCampaignSession:
    """Offline requests.Session stand-in for Relay campaign API calls."""

    def request(self, method, url, *, json=None, params=None, headers=None, timeout=None, **_kwargs):
        return _campaign_requests.handle_campaign_request(method, url, json=json)


class StubRelayCampaignRequests:
    """Requests-module stand-in for email_app.services.relay_campaigns."""

    Timeout = Timeout
    RequestException = RequestException
    exceptions = requests.exceptions
    Session = _StubCampaignSession

    def __init__(self):
        self.calls = []
        self._campaigns = {}
        self._lock = Lock()

    def handle_campaign_request(self, method, url, *, json=None):
        with self._lock:
            self.calls.append({"method": method, "url": url, "json": json})
            if url.endswith("/api/transient-recipient-lists/transactional-send"):
                members = (json or {}).get("members") or []
                ids = [f"relay-test-msg-{idx}" for idx in range(len(members))]
                return StubRelayResponse({"queued_message_ids": ids})
            if method == "POST" and "/api/campaigns/" in url and url.endswith("/submit"):
                key = url.split("/api/campaigns/", 1)[1].rsplit("/submit", 1)[0]
                return self._submit_campaign(key, json or {})
            if method == "GET" and "/api/campaigns/" in url:
                key = url.split("/api/campaigns/", 1)[1].split("?", 1)[0]
                return self._get_campaign(key)
        return StubRelayResponse({"ok": True})

    def _submit_campaign(self, key, payload):
        recipients = payload.get("recipients") or []
        if key in self._campaigns:
            campaign = self._campaigns[key]
            return StubRelayResponse({
                "campaign": campaign,
                "created": False,
                "queued": False,
                "already_queued": True,
                "recipient_count": campaign["recipient_count"],
                "skipped_count": 0,
            })
        campaign = {
            "external_key": key,
            "status": "queued",
            "recipient_count": len(recipients),
            "sent_count": 0,
            "skipped_count": 0,
            "pending_count": len(recipients),
            "delivered_count": 0,
            "unique_open_count": 0,
            "open_count": 0,
            "unique_click_count": 0,
            "click_count": 0,
            "unsubscribe_count": 0,
            "bounce_count": 0,
            "complaint_count": 0,
        }
        self._campaigns[key] = campaign
        resp = StubRelayResponse({
            "campaign": campaign,
            "created": True,
            "queued": True,
            "already_queued": False,
            "recipient_count": len(recipients),
            "skipped_count": 0,
        })
        resp.status_code = 201
        return resp

    def _get_campaign(self, key):
        campaign = self._campaigns.get(key)
        if campaign is None:
            resp = StubRelayResponse({"error": "not_found"})
            resp.status_code = 404
            return resp
        resp = StubRelayResponse({"campaign": campaign})
        resp.status_code = 200
        return resp


def _message_id(idempotency_key):
    digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:16]
    return f"relay-test-{digest}"


_transport = None
_campaign_requests = None


def _test_relay_config(key, default="", **kwargs):
    """Use configured Relay values before offline adapter defaults."""
    values = {
        "RELAY_BASE_URL": "https://relay.test",
        "RELAY_API_KEY": "test-relay-key",
    }
    from integrations.config import get_config  # noqa: PLC0415

    if key in values:
        configured = get_config(key, "", **kwargs)
        if configured not in (None, ""):
            return configured
        return values[key]
    return get_config(key, default, **kwargs)


def install_test_relay_transactional_transport():
    """Install the offline transport on Relay transactional and campaign modules."""
    global _transport, _campaign_requests
    from email_app import relay_transactional  # noqa: PLC0415
    from email_app.services import relay_campaigns  # noqa: PLC0415

    if _transport is None:
        _transport = StubRelayTransactionalTransport()
    if _campaign_requests is None:
        _campaign_requests = StubRelayCampaignRequests()
    relay_transactional.requests = _transport
    relay_transactional.get_config = _test_relay_config
    relay_campaigns.requests = _campaign_requests
    relay_campaigns.get_config = _test_relay_config
    return _transport
