"""``asl users course-progress`` wiring tests (issue #1836)."""

from __future__ import annotations

import json

import pytest
from asl_cli.cli import cli
from asl_cli.commands import users as users_module
from click.testing import CliRunner

EMAIL = "gmajivu@gmail.com"

PAYLOAD = {
    "user": {"email": EMAIL, "display_name": "Gabriel Majivu"},
    "count": 1,
    "completions": [
        {
            "id": 91,
            "completed_at": "2026-09-20T15:04:00+00:00",
            "course": {
                "id": 4,
                "slug": "buildcamp",
                "title": "AI Engineering Buildcamp",
            },
            "module": {
                "id": 12,
                "slug": "week-1",
                "title": "Week 1",
                "sort_order": 1,
                "parent": None,
            },
            "unit": {
                "id": 40,
                "slug": "intro",
                "title": "Intro",
                "kind": "lesson",
                "sort_order": 1,
                "is_bonus": False,
                "url": "/courses/buildcamp/week-1/intro",
            },
        },
    ],
}


class RecordingClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get(self, path, **kwargs):
        self.calls.append((path, kwargs))
        return self.payload


@pytest.fixture
def client(monkeypatch):
    recorder = RecordingClient(json.loads(json.dumps(PAYLOAD)))
    monkeypatch.setattr(users_module, "get_client", lambda: recorder)
    return recorder


def test_course_progress_gets_the_member_path(client):
    result = CliRunner().invoke(cli, ["users", "course-progress", EMAIL])
    assert result.exit_code == 0, result.output
    assert client.calls == [(f"/api/users/{EMAIL}/course-progress", {})]
    assert json.loads(result.output) == PAYLOAD


def test_course_and_kind_are_query_params(client):
    result = CliRunner().invoke(cli, [
        "users", "course-progress", EMAIL,
        "--course", "buildcamp",
        "--kind", "lesson",
    ])
    assert result.exit_code == 0, result.output
    assert client.calls == [(
        f"/api/users/{EMAIL}/course-progress",
        {"params": {"course": "buildcamp", "kind": "lesson"}},
    )]


@pytest.mark.parametrize("fmt", ["json", "raw"])
def test_json_and_raw_print_the_envelope(client, fmt):
    result = CliRunner().invoke(
        cli, ["users", "course-progress", EMAIL, "--format", fmt],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == PAYLOAD


def test_table_prints_flattened_columns(client):
    result = CliRunner().invoke(
        cli, ["users", "course-progress", EMAIL, "--format", "table"],
    )
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[0].split() == [
        "completed_at",
        "course_slug",
        "module_title",
        "unit_kind",
        "unit_title",
        "unit_url",
    ]
    cells = [
        "2026-09-20T15:04:00+00:00",
        "buildcamp",
        "Week 1",
        "lesson",
        "Intro",
        "/courses/buildcamp/week-1/intro",
    ]
    positions = [lines[2].index(cell) for cell in cells]
    assert positions == sorted(positions)
    assert "display_name" not in result.output
    assert "Gabriel" not in result.output


def test_help_describes_marked_complete(client):
    result = CliRunner().invoke(cli, ["users", "course-progress", "--help"])
    assert result.exit_code == 0, result.output
    assert "Course units this member has marked complete." in result.output
    assert client.calls == []
