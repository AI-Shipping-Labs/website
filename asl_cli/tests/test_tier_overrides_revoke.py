"""``asl tier-overrides revoke`` wiring tests."""

from __future__ import annotations

import json

import pytest
from asl_cli.cli import cli
from asl_cli.commands import tier_overrides as tier_overrides_module
from click.testing import CliRunner

PAYLOAD = {
    "dry_run": False,
    "revoked": 1,
    "no_active_override": 0,
    "user_not_found": 1,
    "malformed": 0,
    "results": [
        {"email": "a@example.com", "status": "revoked", "overrides": []},
        {"email": "b@example.com", "status": "user_not_found"},
    ],
}


class RecordingClient:
    def __init__(self):
        self.calls = []

    def post(self, path, **kwargs):
        self.calls.append((path, kwargs))
        return PAYLOAD


@pytest.fixture
def client(monkeypatch):
    recorder = RecordingClient()
    monkeypatch.setattr(tier_overrides_module, "get_client", lambda: recorder)
    return recorder


def test_revoke_posts_every_email(client):
    result = CliRunner().invoke(
        cli, ["tier-overrides", "revoke", "a@example.com", "b@example.com"],
    )
    assert result.exit_code == 0, result.output
    assert client.calls == [(
        "/api/tier-overrides/revoke",
        {"json_body": {
            "emails": ["a@example.com", "b@example.com"],
            "dry_run": False,
        }},
    )]
    assert json.loads(result.output) == PAYLOAD


def test_dry_run_flag_is_sent(client):
    result = CliRunner().invoke(
        cli, ["tier-overrides", "revoke", "a@example.com", "--dry-run"],
    )
    assert result.exit_code == 0, result.output
    assert client.calls[0][1]["json_body"]["dry_run"] is True


def test_revoke_requires_an_email(client):
    result = CliRunner().invoke(cli, ["tier-overrides", "revoke"])
    assert result.exit_code != 0
    assert client.calls == []
