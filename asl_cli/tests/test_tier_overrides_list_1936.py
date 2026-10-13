"""``asl tier-overrides list`` and ``revoke --ineffective-only`` (issue #1936)."""

from __future__ import annotations

import json

import pytest
from asl_cli.cli import cli
from asl_cli.commands import tier_overrides as tier_overrides_module
from click.testing import CliRunner

LIST_PAYLOAD = {
    "count": 1,
    "total": 3,
    "effective_count": 2,
    "ineffective_count": 1,
    "overrides": [
        {
            "id": 7,
            "email": "leftover@example.com",
            "tier": "basic",
            "stored_tier": "main",
            "tier_at_grant": "main",
            "raises_access": False,
            "source": "manual",
            "created_at": "2026-09-01T10:00:00+00:00",
            "expires_at": "2026-12-01T10:00:00+00:00",
            "granted_by": None,
        },
    ],
}

REVOKE_PAYLOAD = {
    "dry_run": False,
    "revoked": 1,
    "no_active_override": 0,
    "user_not_found": 0,
    "malformed": 0,
    "results": [
        {"email": "leftover@example.com", "status": "revoked", "overrides": []},
    ],
}


class RecordingClient:
    def __init__(self):
        self.calls = []

    def get(self, path, **kwargs):
        self.calls.append(("GET", path, kwargs))
        return LIST_PAYLOAD

    def post(self, path, **kwargs):
        self.calls.append(("POST", path, kwargs))
        return REVOKE_PAYLOAD


@pytest.fixture
def client(monkeypatch):
    recorder = RecordingClient()
    monkeypatch.setattr(tier_overrides_module, "get_client", lambda: recorder)
    return recorder


def test_list_ineffective_sends_status_and_prints_rows(client):
    result = CliRunner().invoke(
        cli, ["tier-overrides", "list", "--status", "ineffective"],
    )
    assert result.exit_code == 0, result.output
    assert client.calls == [
        ("GET", "/api/tier-overrides", {"params": {"status": "ineffective"}}),
    ]
    assert json.loads(result.output) == LIST_PAYLOAD


def test_list_defaults_to_all_and_passes_limit(client):
    result = CliRunner().invoke(cli, ["tier-overrides", "list", "--limit", "5"])
    assert result.exit_code == 0, result.output
    assert client.calls[0][2] == {"params": {"status": "all", "limit": 5}}


def test_list_table_shows_override_rows(client):
    result = CliRunner().invoke(
        cli, ["tier-overrides", "list", "--status", "ineffective", "-f", "table"],
    )
    assert result.exit_code == 0, result.output
    assert "leftover@example.com" in result.output
    assert "stored_tier" in result.output


def test_list_rejects_unknown_status(client):
    result = CliRunner().invoke(cli, ["tier-overrides", "list", "--status", "nope"])
    assert result.exit_code != 0
    assert client.calls == []


def test_revoke_ineffective_only_flag_is_sent(client):
    result = CliRunner().invoke(
        cli,
        [
            "tier-overrides", "revoke", "leftover@example.com",
            "--ineffective-only",
        ],
    )
    assert result.exit_code == 0, result.output
    assert client.calls == [(
        "POST",
        "/api/tier-overrides/revoke",
        {"json_body": {
            "emails": ["leftover@example.com"],
            "dry_run": False,
            "ineffective_only": True,
        }},
    )]


def test_revoke_without_flag_does_not_send_ineffective_only(client):
    result = CliRunner().invoke(
        cli, ["tier-overrides", "revoke", "leftover@example.com"],
    )
    assert result.exit_code == 0, result.output
    assert "ineffective_only" not in client.calls[0][2]["json_body"]
