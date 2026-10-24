"""Native one-request proof mode never follows redirects or retries sends."""

import json

import httpx
import pytest
from asl_cli.cli import cli
from asl_cli.client import APIError, Client
from asl_cli.commands import raw as raw_module
from click.testing import CliRunner

from asl_cli import client as client_module


def capture_client(monkeypatch, handler):
    monkeypatch.setattr(client_module, "resolve_staff_token", lambda: "synthetic-token")
    api = Client(base_url="https://example.invalid")
    api._http.close()
    requests = []

    def capture(request):
        requests.append(request)
        return handler(request)

    api._http = httpx.Client(base_url=api.base_url, follow_redirects=True,
        transport=httpx.MockTransport(capture))
    sleeps = []
    api._sleep = sleeps.append
    return api, requests, sleeps


@pytest.mark.parametrize("status", [300, 301, 302, 303, 304, 307, 308, 399])
def test_single_attempt_rejects_redirect_without_requesting_destination(monkeypatch, status):
    api, requests, sleeps = capture_client(monkeypatch,
        lambda request: httpx.Response(status, headers={"Location": "/destination"}))
    with api, pytest.raises(APIError) as error:
        api.post("/api/proof", json_body={"proof": True}, single_attempt=True)
    assert error.value.status == status
    assert [request.url.path for request in requests] == ["/api/proof"]
    assert json.loads(requests[0].content) == {"proof": True}
    assert sleeps == []


@pytest.mark.parametrize("status,code", [(429, "rate_limited"), (429, "too_many_concurrent_requests"), (503, "unavailable")])
def test_single_attempt_rejections_never_retry_or_sleep(monkeypatch, status, code):
    api, requests, sleeps = capture_client(monkeypatch,
        lambda request: httpx.Response(status, json={"error": "Rejected", "code": code}))
    with api, pytest.raises(APIError) as error:
        api.post("/api/proof", single_attempt=True)
    assert error.value.status == status
    assert len(requests) == 1
    assert sleeps == []


def test_single_attempt_timeout_is_not_retried(monkeypatch):
    def timeout(request):
        raise httpx.ReadTimeout("Synthetic timeout", request=request)

    api, requests, sleeps = capture_client(monkeypatch, timeout)
    with api, pytest.raises(httpx.ReadTimeout):
        api.post("/api/proof", single_attempt=True)
    assert len(requests) == 1
    assert sleeps == []


def test_actual_raw_flag_uses_real_client_single_attempt(monkeypatch):
    api, requests, sleeps = capture_client(monkeypatch,
        lambda request: httpx.Response(307, headers={"Location": "/destination"}))
    monkeypatch.setattr(raw_module, "get_client", lambda: api)
    with api:
        result = CliRunner().invoke(cli, ["raw", "POST", "/api/proof", "--single-attempt", "--data", '{"proof":true}'])
    assert isinstance(result.exception, APIError)
    assert result.exception.status == 307
    assert [request.url.path for request in requests] == ["/api/proof"]
    assert requests[0].headers["Authorization"] == "Token synthetic-token"
    assert sleeps == []


@pytest.mark.parametrize("status,body,expected", [(200, {"accepted": True}, {"accepted": True}), (204, None, None)])
def test_single_attempt_retains_success_and_empty_response_decoding(monkeypatch, status, body, expected):
    api, requests, sleeps = capture_client(monkeypatch,
        lambda request: httpx.Response(status, json=body))
    with api:
        assert api.post("/api/proof", single_attempt=True) == expected
    assert len(requests) == 1
    assert sleeps == []


def test_default_mode_still_follows_redirects(monkeypatch):
    def redirect_then_accept(request):
        if request.url.path == "/api/proof":
            return httpx.Response(307, headers={"Location": "/destination"})
        return httpx.Response(200, json={"accepted": True})

    api, requests, sleeps = capture_client(monkeypatch, redirect_then_accept)
    with api:
        assert api.post("/api/proof") == {"accepted": True}
    assert [request.url.path for request in requests] == ["/api/proof", "/destination"]
