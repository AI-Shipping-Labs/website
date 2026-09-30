"""``asl sprints course-cohorts`` / ``course-cohort-update`` /
``course-coursework-inventory`` wiring tests."""

from __future__ import annotations

import json

import pytest
from asl_cli.cli import cli
from asl_cli.commands import sprints as sprints_module
from click.testing import CliRunner

RESPONSE = {"course": "ai-buildcamp", "cohorts": []}


class RecordingClient:
    def __init__(self):
        self.calls = []

    def get(self, path, **kwargs):
        self.calls.append(("GET", path, kwargs))
        return RESPONSE

    def patch(self, path, **kwargs):
        self.calls.append(("PATCH", path, kwargs))
        return RESPONSE


@pytest.fixture
def client(monkeypatch):
    recorder = RecordingClient()
    monkeypatch.setattr(sprints_module, "get_client", lambda: recorder)
    return recorder


def test_course_cohorts_lists_cohorts(client):
    result = CliRunner().invoke(cli, ["sprints", "course-cohorts", "ai-buildcamp"])
    assert result.exit_code == 0, result.output
    assert client.calls == [("GET", "/api/courses/ai-buildcamp/cohorts", {})]
    assert json.loads(result.output) == RESPONSE


def test_course_coursework_inventory_gets_the_inventory(client):
    result = CliRunner().invoke(cli, [
        "sprints", "course-coursework-inventory", "ai-buildcamp",
    ])
    assert result.exit_code == 0, result.output
    assert client.calls == [("GET", "/api/courses/ai-buildcamp/coursework-inventory", {})]
    assert json.loads(result.output) == RESPONSE


@pytest.mark.parametrize(
    ("value", "expected"),
    [("buildcamp-office-hours-cohort-4", "buildcamp-office-hours-cohort-4"), ("5", 5)],
)
def test_cohort_update_sends_series_slug_or_id(client, value, expected):
    result = CliRunner().invoke(cli, [
        "sprints", "course-cohort-update", "ai-buildcamp", "4", "--event-series", value,
    ])
    assert result.exit_code == 0, result.output
    assert client.calls == [(
        "PATCH", "/api/courses/ai-buildcamp/cohorts/4",
        {"json_body": {"event_series": expected}},
    )]


def test_cohort_update_sends_dates(client):
    result = CliRunner().invoke(cli, [
        "sprints", "course-cohort-update", "ai-buildcamp", "4",
        "--start-date", "2026-09-14", "--end-date", "2026-11-09",
    ])
    assert result.exit_code == 0, result.output
    assert client.calls[0][2] == {
        "json_body": {"start_date": "2026-09-14", "end_date": "2026-11-09"},
    }


def test_cohort_update_without_changes_is_a_usage_error(client):
    result = CliRunner().invoke(cli, ["sprints", "course-cohort-update", "ai-buildcamp", "4"])
    assert result.exit_code == 2
    assert client.calls == []
