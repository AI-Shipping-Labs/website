"""``asl campaigns relay-proof`` follows the documented API contract."""

from __future__ import annotations

from asl_cli.cli import cli
from asl_cli.commands import campaigns_relay_proof as proof_module
from click.testing import CliRunner


class RecordingClient:
    def __init__(self):
        self.calls = []

    def post(self, path, *, json_body=None):
        self.calls.append(("POST", path, json_body))
        return {"campaign_key": "k", "recipient_count": 200, "queued": True}

    def get(self, path):
        self.calls.append(("GET", path, None))
        return {"campaign_key": "k", "sent_count": 200, "sent_at": "2026-11-10T17:00:00Z"}


def test_dry_run_posts_the_dry_run_route(monkeypatch):
    client = RecordingClient()
    monkeypatch.setattr(proof_module, "get_client", lambda: client)

    result = CliRunner().invoke(cli, ["campaigns", "relay-proof", "42", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert client.calls == [("POST", "/api/campaigns/42/relay-proof/dry-run", None)]


def test_confirm_posts_the_signed_key(monkeypatch):
    client = RecordingClient()
    monkeypatch.setattr(proof_module, "get_client", lambda: client)

    result = CliRunner().invoke(
        cli,
        ["campaigns", "relay-proof", "42", "--confirm", "aishl-test-42-aa-bb"],
    )

    assert result.exit_code == 0, result.output
    assert client.calls == [
        (
            "POST",
            "/api/campaigns/42/relay-proof/confirm",
            {"campaign_key": "aishl-test-42-aa-bb"},
        ),
    ]


def test_dry_run_and_confirm_are_mutually_exclusive(monkeypatch):
    client = RecordingClient()
    monkeypatch.setattr(proof_module, "get_client", lambda: client)

    result = CliRunner().invoke(
        cli,
        ["campaigns", "relay-proof", "42", "--dry-run", "--confirm", "key"],
    )

    assert result.exit_code != 0
    assert client.calls == []


def test_passing_neither_flag_is_a_usage_error(monkeypatch):
    client = RecordingClient()
    monkeypatch.setattr(proof_module, "get_client", lambda: client)

    result = CliRunner().invoke(cli, ["campaigns", "relay-proof", "42"])

    assert result.exit_code != 0
    assert client.calls == []


def test_stats_reads_the_key_route(monkeypatch):
    client = RecordingClient()
    monkeypatch.setattr(proof_module, "get_client", lambda: client)

    result = CliRunner().invoke(
        cli,
        ["campaigns", "relay-proof-stats", "42", "aishl-test-42-aa-bb"],
    )

    assert result.exit_code == 0, result.output
    assert client.calls == [("GET", "/api/campaigns/42/relay-proof/aishl-test-42-aa-bb", None)]
