"""Device-login CLI tests (in-process, no network)."""

from __future__ import annotations

import json
import os
import stat

import httpx
import pytest
from asl_cli.cli import cli
from click.testing import CliRunner

from asl_cli import config
from asl_cli import device_flow as device_flow_module


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch, tmp_path):
    for key in ("ASL_API_TOKEN", "API_SHIPPING_LABS_API_TOKEN", "ASL_BASE_URL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(config, "_find_env_file", lambda: None)
    monkeypatch.setenv("ASL_CREDENTIALS_PATH", str(tmp_path / "credentials.json"))


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class FakeHTTP:
    def __init__(self, script):
        self.script = list(script)
        self.posts = []

    def post(self, path, json=None):
        self.posts.append((path, json))
        return self.script.pop(0)


def _offer():
    return {
        "device_code": "cbdl_secret",
        "user_code": "BCDF-GHJK",
        "verification_uri": "https://aishippinglabs.com/device/",
        "verification_uri_complete": "https://aishippinglabs.com/device/?code=BCDF-GHJK",
        "expires_in": 600,
        "interval": 0.01,
    }


def test_device_flow_prints_code_and_url_but_never_device_code():
    printed = []
    http = FakeHTTP(
        [
            FakeResponse(200, _offer()),
            FakeResponse(400, {"error": "authorization_pending"}),
            FakeResponse(200, {"token": "tok", "user": {"email": "op@example.com"}}),
        ]
    )
    credential = device_flow_module.device_login(
        "https://aishippinglabs.com", printer=printed.append, client=http
    )
    assert credential["token"] == "tok"
    assert any("BCDF-GHJK" in line for line in printed)
    assert any("/device/?code=BCDF-GHJK" in line for line in printed)
    assert not any("cbdl_secret" in line for line in printed)


def test_device_flow_denied_and_expired():
    with pytest.raises(device_flow_module.DeviceLoginError) as denied:
        device_flow_module.device_login(
            "https://aishippinglabs.com",
            printer=lambda line: None,
            client=FakeHTTP(
                [FakeResponse(200, _offer()), FakeResponse(400, {"error": "access_denied"})]
            ),
        )
    assert denied.value.code == "access_denied"
    with pytest.raises(device_flow_module.DeviceLoginError) as expired:
        device_flow_module.device_login(
            "https://aishippinglabs.com",
            printer=lambda line: None,
            client=FakeHTTP(
                [FakeResponse(200, _offer()), FakeResponse(400, {"error": "expired_token"})]
            ),
        )
    assert expired.value.code == "expired_token"


def test_stored_credential_round_trip_with_strict_perms():
    path = config.write_stored_credential(
        base_url="https://aishippinglabs.com", token="tok", email="op@example.com"
    )
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert config.read_stored_credential("https://aishippinglabs.com") == "tok"
    assert config.resolve_staff_token() == "tok"


def test_stored_credential_ignored_for_other_base_url():
    config.write_stored_credential(
        base_url="https://aishippinglabs.com", token="tok", email="op@example.com"
    )
    assert config.read_stored_credential("http://localhost:8000") is None


def test_env_token_wins_over_stored_file(monkeypatch):
    config.write_stored_credential(
        base_url="https://aishippinglabs.com", token="stored", email="op@example.com"
    )
    monkeypatch.setenv("ASL_API_TOKEN", "from-env")
    assert config.resolve_staff_token() == "from-env"


def test_login_command_stores_credential(monkeypatch):
    from asl_cli.commands import auth as auth_module

    monkeypatch.setattr(
        auth_module,
        "device_login",
        lambda *a, **k: {"token": "tok", "user": {"email": "op@example.com"}},
    )
    result = CliRunner().invoke(cli, ["login"])
    assert result.exit_code == 0, result.output
    assert "Signed in as op@example.com." in result.output
    assert "0600" in result.output
    assert config.read_stored_credential("https://aishippinglabs.com") == "tok"


def test_logout_command_clears_file():
    config.write_stored_credential(
        base_url="https://aishippinglabs.com", token="tok", email="op@example.com"
    )
    result = CliRunner().invoke(cli, ["logout"])
    assert result.exit_code == 0, result.output
    assert config.read_stored_credential("https://aishippinglabs.com") is None


def test_whoami_command_proves_credential(monkeypatch):
    from asl_cli.commands import auth as auth_module

    class RecordingClient:
        def __init__(self):
            self.calls = []

        def get(self, path, **kwargs):
            self.calls.append(path)
            return {"email": "op@example.com"}

    client = RecordingClient()
    monkeypatch.setattr(auth_module, "get_client", lambda: client)
    result = CliRunner().invoke(cli, ["whoami"])
    assert result.exit_code == 0, result.output
    assert "op@example.com" in result.output
    assert client.calls == ["/api/whoami"]


def test_whoami_unreachable_server_is_an_error(monkeypatch):
    from asl_cli.commands import auth as auth_module

    class BrokenClient:
        def get(self, path, **kwargs):
            raise httpx.ConnectError("down")

    monkeypatch.setattr(auth_module, "get_client", lambda: BrokenClient())
    result = CliRunner().invoke(cli, ["whoami"])
    assert result.exit_code != 0
