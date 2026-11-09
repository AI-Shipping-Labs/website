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
from asl_cli.commands import campaigns_read as campaigns_read_module
from click.testing import CliRunner

RESPONSE = {"id": 7, "status": "draft", "scheduled_at": None}


class RecordingClient:
    def __init__(self):
        self.calls = []
        self.campaign = {
            "id": 7,
            "target_min_level": 20,
            "target_tags_any": ["sprint:may-2026"],
            "target_tags_none": ["bounced"],
            "slack_filter": "yes",
            "audience_verification": "verified_only",
            "target_event": None,
        }

    def post(self, path, **kwargs):
        self.calls.append(("POST", path, kwargs))
        if path == "/api/campaigns/recipient-count":
            return {"recipient_count": 42}
        return RESPONSE

    def get(self, path, **kwargs):
        self.calls.append(("GET", path, kwargs))
        if path.endswith("/recipients"):
            return {
                "campaign_id": 7,
                "count": 1,
                "mode": "preview",
                "recipients": [{"email": "member@example.com", "delivery_state": "eligible"}],
            }
        if path.endswith("/waves"):
            return {
                "campaign_id": 7,
                "waves": [{"number": 1, "state": "monitoring", "recipient_count": 20}],
            }
        return self.campaign


@pytest.fixture
def client(monkeypatch):
    recorder = RecordingClient()
    monkeypatch.setattr(campaigns_module, "get_client", lambda: recorder)
    monkeypatch.setattr(campaigns_read_module, "get_client", lambda: recorder)
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


def test_send_dry_run_counts_saved_audience_without_sending(client):
    result = run("send", "7", "--dry-run", "--format", "raw")

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {
        "campaign_id": 7,
        "recipient_count": 42,
        "tier_filter": "main",
        "target_min_level": 20,
    }
    assert client.calls == [
        ("GET", "/api/campaigns/7", {}),
        (
            "POST", "/api/campaigns/recipient-count",
            {"json_body": {
                "target_min_level": 20,
                "target_tags_any": ["sprint:may-2026"],
                "target_tags_none": ["bounced"],
                "slack_filter": "yes",
                "audience_verification": "verified_only",
                "target_event": None,
            }},
        ),
    ]


def test_recipient_count_posts_audience_flags(client):
    result = run(
        "recipient-count", "--target-min-level", "basic",
        "--target-tags-any", "one,two", "--slack-filter", "yes",
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {
        "recipient_count": 42,
        "filters": {
            "target_min_level": 10,
            "target_tags_any": ["one", "two"],
            "slack_filter": "yes",
        },
    }
    assert client.calls == [
        (
            "POST", "/api/campaigns/recipient-count",
            {"json_body": {
                "target_min_level": 10,
                "target_tags_any": ["one", "two"],
                "slack_filter": "yes",
            }},
        ),
    ]


def test_recipient_count_can_use_saved_campaign_filters(client):
    result = run("recipient-count", "--campaign-id", "7")

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["recipient_count"] == 42
    assert payload["campaign_id"] == 7
    assert payload["filters"]["target_min_level"] == 20
    assert client.calls[0] == ("GET", "/api/campaigns/7", {})
    assert client.calls[1][1] == "/api/campaigns/recipient-count"


def test_recipient_count_rejects_saved_campaign_with_extra_filters(client):
    result = run(
        "recipient-count", "--campaign-id", "7", "--target-min-level", "main",
    )

    assert result.exit_code != 0
    assert "cannot be combined" in result.output
    assert client.calls == []


def test_campaign_recipients_supports_table_and_json_alias(client):
    table = run("recipients", "7", "--format", "table")
    assert table.exit_code == 0, table.output
    assert "email" in table.output
    assert "member@example.com" in table.output
    assert client.calls[-1] == ("GET", "/api/campaigns/7/recipients", {})

    structured = run("recipients", "7", "--json")
    assert structured.exit_code == 0, structured.output
    assert json.loads(structured.output)["recipients"][0]["email"] == "member@example.com"


def test_campaign_waves_supports_table_and_json_alias(client):
    table = run("waves", "7", "--format", "table")
    assert table.exit_code == 0, table.output
    assert "recipient_count" in table.output
    assert "monitoring" in table.output
    assert client.calls[-1] == ("GET", "/api/campaigns/7/waves", {})

    structured = run("waves", "7", "--json")
    assert structured.exit_code == 0, structured.output
    assert json.loads(structured.output)["waves"][0]["number"] == 1


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
    assert "--dry-run" in result.output


def test_test_send_help_mentions_recipient():
    result = run("test-send", "--help")
    assert "--recipient" in result.output
