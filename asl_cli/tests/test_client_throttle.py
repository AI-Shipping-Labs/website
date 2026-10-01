"""``asl`` backs off on the server's per-token throttle 429s (issue #1854)."""

from __future__ import annotations

import httpx
import pytest
from asl_cli.client import MAX_THROTTLE_RETRIES, APIError, Client

from asl_cli import client as client_module


def _throttled(code="rate_limited", retry_after="3"):
    return httpx.Response(
        429,
        headers={"Retry-After": retry_after},
        json={"error": "Too many requests for this API token.", "code": code},
    )


def _client(monkeypatch, responses):
    monkeypatch.setattr(client_module, "resolve_staff_token", lambda: "tok")
    monkeypatch.setattr(client_module.random, "uniform", lambda low, high: 0.0)
    sent = []
    queue = list(responses)

    def handler(request):
        sent.append(request)
        return queue.pop(0)

    api = Client(base_url="https://example.test")
    api._http = httpx.Client(
        base_url="https://example.test", transport=httpx.MockTransport(handler),
    )
    sleeps = []
    api._sleep = sleeps.append
    return api, sent, sleeps


def test_throttled_request_is_retried_after_retry_after(monkeypatch):
    api, sent, sleeps = _client(
        monkeypatch,
        [_throttled(retry_after="3"), httpx.Response(200, json={"ok": True})],
    )

    assert api.post("/api/campaigns/recipient-count", json_body={}) == {"ok": True}
    assert len(sent) == 2
    assert sleeps == [3.0]


def test_concurrency_429_backs_off_exponentially(monkeypatch):
    api, sent, sleeps = _client(
        monkeypatch,
        [
            _throttled("too_many_concurrent_requests", "1"),
            _throttled("too_many_concurrent_requests", "1"),
            _throttled("too_many_concurrent_requests", "1"),
            httpx.Response(200, json={"email": "a@b.c"}),
        ],
    )

    assert api.get("/api/users/a@b.c") == {"email": "a@b.c"}
    assert sleeps == [1.0, 2.0, 4.0]


def test_retries_are_bounded_then_the_429_is_raised(monkeypatch):
    api, sent, sleeps = _client(
        monkeypatch, [_throttled(retry_after="1")] * (MAX_THROTTLE_RETRIES + 1),
    )

    with pytest.raises(APIError) as excinfo:
        api.get("/api/users/a@b.c")

    assert excinfo.value.status == 429
    assert len(sent) == MAX_THROTTLE_RETRIES + 1
    assert len(sleeps) == MAX_THROTTLE_RETRIES
    assert max(sleeps) <= client_module.MAX_BACKOFF_SECONDS


def test_final_jittered_delay_is_capped_at_thirty_seconds(monkeypatch):
    monkeypatch.setattr(client_module.random, "uniform", lambda low, high: high)
    response = _throttled(retry_after="30")

    delay = client_module.throttle_backoff_seconds(response, attempt=4)

    assert delay == client_module.MAX_BACKOFF_SECONDS


def test_non_throttle_429_is_not_retried(monkeypatch):
    api, sent, sleeps = _client(
        monkeypatch,
        [httpx.Response(429, json={"error": "Too many users", "code": "too_many_users"})],
    )

    with pytest.raises(APIError):
        api.post("/api/tier-reconcile", json_body={})

    assert len(sent) == 1
    assert sleeps == []
