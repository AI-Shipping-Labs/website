"""CLI wiring tests via Click's ``CliRunner`` (in-process, no HTTP)."""

from __future__ import annotations

import json

import click
import pytest
from asl_cli.cli import cli
from asl_cli.commands import events as events_module
from asl_cli.commands import groups
from asl_cli.commands import sync as sync_module
from asl_cli.commands._shared import TIER_LEVELS, TierLevel
from click.testing import CliRunner

# Pinned contract: every command group that must stay registered. A removed
# or renamed group makes ``test_expected_groups_are_registered`` fail before
# the parametrized help guard even runs.
EXPECTED_GROUPS = {
    "articles",
    "campaigns",
    "cleanup-gates",
    "contacts",
    "crm-export",
    "event-series",
    "events",
    "hosts",
    "integrations",
    "onboarding",
    "openapi",
    "plans",
    "raw",
    "redirects",
    "ses-events",
    "sprints",
    "sync",
    "tier-overrides",
    "tier-reconcile",
    "triggers",
    "users",
    "utm-campaigns",
    "worker",
}

GROUP_NAMES = sorted(g.name for g in groups)


def test_help_exits_zero():
    result = CliRunner().invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "asl" in result.output


def test_version_exits_zero():
    result = CliRunner().invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert "asl" in result.output


def test_expected_groups_are_registered():
    assert {g.name for g in groups} == EXPECTED_GROUPS


@pytest.mark.parametrize("group", GROUP_NAMES)
def test_every_group_help_exits_zero(group):
    result = CliRunner().invoke(cli, [group, "--help"])
    assert result.exit_code == 0, result.output


def test_raw_help_documents_method_path_escape_hatch():
    result = CliRunner().invoke(cli, ["raw", "--help"])
    assert result.exit_code == 0
    # Usage shows the METHOD choices followed by the PATH argument.
    assert "PATH" in result.output
    assert "get|post|patch|put|delete" in result.output.lower()
    assert "/api/events" in result.output


class RecordingSyncClient:
    def __init__(self):
        self.calls = []

    def get(self, path, **kwargs):
        self.calls.append((path, kwargs))
        return {"ok": True}


def test_sync_history_constructs_all_filter_and_paging_flags(monkeypatch):
    client = RecordingSyncClient()
    monkeypatch.setattr(sync_module, "get_client", lambda: client)
    result = CliRunner().invoke(cli, [
        "sync", "history", "--source", "source-id", "--status", "failed",
        "--page", "3", "--page-size", "75", "--format", "json",
    ])
    assert result.exit_code == 0, result.output
    assert client.calls == [
        ("/api/sync/history", {"params": {
            "page": 3, "page_size": 75, "source": "source-id", "status": "failed",
        }}),
    ]


def test_sync_history_detail_constructs_history_id_path(monkeypatch):
    client = RecordingSyncClient()
    monkeypatch.setattr(sync_module, "get_client", lambda: client)
    result = CliRunner().invoke(
        cli, ["sync", "history-detail", "batch-id", "--format", "json"],
    )
    assert result.exit_code == 0, result.output
    assert client.calls == [("/api/sync/history/batch-id", {})]


class RecordingEventsClient:
    def __init__(self):
        self.calls = []

    def post(self, path, *, json_body=None):
        self.calls.append(("POST", path, json_body))
        return {"ok": True, "received": json_body}

    def patch(self, path, *, json_body=None):
        self.calls.append(("PATCH", path, json_body))
        return {"ok": True, "received": json_body}


def test_events_update_timestamps_parses_inline_json_array(monkeypatch):
    client = RecordingEventsClient()
    monkeypatch.setattr(events_module, "get_client", lambda: client)

    result = CliRunner().invoke(
        cli,
        [
            "events",
            "update",
            "office-hours",
            "--recording-url",
            "https://www.youtube.com/watch?v=abc",
            "--timestamps",
            '[{"time":"16:00","title":"Setup"}]',
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert len(client.calls) == 1
    method, path, body = client.calls[0]
    assert method == "PATCH"
    assert path == "/api/events/office-hours"
    assert body["recording_url"] == "https://www.youtube.com/watch?v=abc"
    assert body["timestamps"] == [{"time": "16:00", "title": "Setup"}]
    assert isinstance(body["timestamps"], list)
    assert not isinstance(body["timestamps"], str)


def test_events_create_timestamps_parses_json_file(monkeypatch, tmp_path):
    client = RecordingEventsClient()
    monkeypatch.setattr(events_module, "get_client", lambda: client)
    chapters_path = tmp_path / "chapters.json"
    chapters = [{"time_seconds": 125, "label": "Build"}]
    chapters_path.write_text(json.dumps(chapters), encoding="utf-8")

    result = CliRunner().invoke(
        cli,
        [
            "events",
            "create",
            "--title",
            "File Chapters Event",
            "--start-datetime",
            "2026-05-05T17:00:00+02:00",
            "--timestamps",
            f"@{chapters_path}",
            "--format",
            "raw",
        ],
    )

    assert result.exit_code == 0, result.output
    assert client.calls == [
        (
            "POST",
            "/api/events",
            {
                "title": "File Chapters Event",
                "start_datetime": "2026-05-05T17:00:00+02:00",
                "timestamps": chapters,
                "status": "upcoming",
                "published": True,
            },
        )
    ]
    assert isinstance(client.calls[0][2]["timestamps"], list)


def test_events_timestamps_rejects_non_array(monkeypatch):
    client = RecordingEventsClient()
    monkeypatch.setattr(events_module, "get_client", lambda: client)

    result = CliRunner().invoke(
        cli,
        [
            "events",
            "update",
            "office-hours",
            "--timestamps",
            '{"time":"16:00","title":"Setup"}',
        ],
    )

    assert result.exit_code != 0
    assert "--timestamps must be a JSON array" in result.output
    assert client.calls == []


# --- Tier-name parsing (used by --required-level / --target-min-level) ---


@pytest.mark.parametrize(
    "name,expected",
    [
        ("open", 0),
        ("registered", 5),
        ("basic", 10),
        ("main", 20),
        ("premium", 30),
    ],
)
def test_tier_names_map_to_levels(name, expected):
    assert TierLevel().convert(name, None, None) == expected
    # The mapping table stays the single source of truth.
    assert TIER_LEVELS[name] == expected


def test_tier_name_parsing_is_case_insensitive():
    assert TierLevel().convert("PREMIUM", None, None) == 30


@pytest.mark.parametrize("raw", ["0", "5", "10", "20", "30", "15"])
def test_tier_accepts_raw_integers(raw):
    assert TierLevel().convert(raw, None, None) == int(raw)


def test_tier_passthrough_of_none_and_int():
    assert TierLevel().convert(None, None, None) is None
    assert TierLevel().convert(25, None, None) == 25


def test_tier_rejects_unknown_value():
    with pytest.raises(click.exceptions.UsageError):
        TierLevel().convert("nonsense", None, None)
