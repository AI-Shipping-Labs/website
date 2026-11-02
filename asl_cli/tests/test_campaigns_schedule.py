"""``asl campaigns schedule`` / ``unschedule`` / ``send`` wiring.

``test-send`` has its own authoritative test file
(``test_campaigns_test_send.py``); only its ``--help`` presence is asserted
here so the dispatch group stays completely enumerated.
"""

from __future__ import annotations

import json

import pytest
from asl_cli.cli import cli
from asl_cli.commands import campaigns as campaigns_module
from click.testing import CliRunner

RESPONSE = {"id": 7, "status": "draft", "scheduled_at": None}


class RecordingClient:
    def __init__(self):
        self.calls = []

    def post(self, path, **kwargs):
        self.calls.append(("POST", path, kwargs))
        return RESPONSE


@pytest.fixture
def client(monkeypatch):
    recorder = RecordingClient()
    monkeypatch.setattr(campaigns_module, "get_client", lambda: recorder)
    return recorder


def run(*args):
    return CliRunner().invoke(cli, ["campaigns", *args])


def test_schedule_posts_at_and_tz(client):
    result = run(
        "schedule", "7", "--at", "2026-11-05T10:00:00", "--tz", "Europe/Berlin",
    )
    assert result.exit_code == 0, result.output
    assert client.calls == [
        (
            "POST",
            "/api/campaigns/7/schedule",
            {"json_body": {
                "scheduled_at": "2026-11-05T10:00:00",
                "scheduled_timezone": "Europe/Berlin",
            }},
        ),
    ]
    assert json.loads(result.output) == RESPONSE


def test_schedule_requires_at(client):
    result = run("schedule", "7")
    assert result.exit_code != 0
    assert client.calls == []


def test_schedule_rejects_missing_tz_for_naive_at(client):
    result = run("schedule", "7", "--at", "2026-11-05T10:00:00")
    assert result.exit_code != 0


def test_schedule_aware_at_without_tz(client):
    result = run("schedule", "7", "--at", "2026-11-05T10:00:00+01:00")
    assert result.exit_code == 0, result.output
    assert client.calls[0][2]["json_body"] == {
        "scheduled_at": "2026-11-05T10:00:00+01:00",
    }


def test_unschedule_posts_without_body(client):
    result = run("unschedule", "7")
    assert result.exit_code == 0, result.output
    assert client.calls == [("POST", "/api/campaigns/7/unschedule", {})]


def test_send_requires_confirmation(client):
    result = run("send", "7")
    assert result.exit_code != 0
    assert client.calls == []


def test_send_with_yes_flag_confirms(client):
    result = run("send", "7", "--yes")
    assert result.exit_code == 0, result.output
    assert client.calls == [("POST", "/api/campaigns/7/send", {})]


def test_send_interactive_confirm_aborts_on_no(client):
    result = CliRunner().invoke(cli, ["campaigns", "send", "7"], input="n\n")
    assert result.exit_code != 0
    assert client.calls == []


@pytest.mark.parametrize(
    "command",
    ["schedule", "unschedule", "send", "test-send"],
)
def test_commands_have_help(command):
    result = run(command, "--help")
    assert result.exit_code == 0, result.output
    assert "Usage:" in result.output


def test_schedule_help_mentions_at_and_tz():
    result = run("schedule", "--help")
    assert "--at" in result.output
    assert "--tz" in result.output
    assert "Europe/Berlin" in result.output


def test_send_help_mentions_yes_flag():
    result = run("send", "--help")
    assert "--yes" in result.output


def test_test_send_help_mentions_recipient():
    result = run("test-send", "--help")
    assert "--recipient" in result.output
