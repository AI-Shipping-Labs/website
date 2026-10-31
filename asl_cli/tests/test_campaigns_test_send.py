"""``asl campaigns test-send`` posts one recipient to the test-send route."""

from __future__ import annotations

from asl_cli.cli import cli
from asl_cli.commands import campaigns as campaigns_module
from click.testing import CliRunner


class RecordingClient:
    def __init__(self):
        self.calls = []

    def post(self, path, *, json_body=None):
        self.calls.append((path, json_body))
        return {"campaign_id": 17, "relay_message_id": "m-1", "status": "accepted"}


def test_test_send_posts_exactly_one_recipient(monkeypatch):
    client = RecordingClient()
    monkeypatch.setattr(campaigns_module, "get_client", lambda: client)

    result = CliRunner().invoke(
        cli, ["campaigns", "test-send", "17", "--recipient", "a@example.com"],
    )

    assert result.exit_code == 0, result.output
    assert client.calls == [
        ("/studio/campaigns/17/test-send", {"test_recipients": "a@example.com"}),
    ]
    assert "accepted" in result.output


def test_test_send_requires_a_recipient(monkeypatch):
    client = RecordingClient()
    monkeypatch.setattr(campaigns_module, "get_client", lambda: client)

    result = CliRunner().invoke(cli, ["campaigns", "test-send", "17"])

    assert result.exit_code != 0
    assert client.calls == []
