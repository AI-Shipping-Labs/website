"""``asl sprints course-enroll`` wiring tests."""

from __future__ import annotations

import json

import pytest
from asl_cli.cli import cli
from asl_cli.commands import sprints as sprints_module
from click.testing import CliRunner

RESPONSE = {
    "enrolled": 1,
    "already_enrolled": 0,
    "under_tier": [],
    "unknown_emails": [],
    "cohort_enrollments": [
        {"user_email": "a@example.com", "cohort": "4", "cohort_name": "Cohort 4", "created": True},
    ],
}


class RecordingClient:
    def __init__(self):
        self.calls = []

    def post(self, path, **kwargs):
        self.calls.append((path, kwargs))
        return RESPONSE


@pytest.fixture
def client(monkeypatch):
    recorder = RecordingClient()
    monkeypatch.setattr(sprints_module, "get_client", lambda: recorder)
    return recorder


def test_course_enroll_with_cohort_posts_cohort_key(client):
    result = CliRunner().invoke(cli, [
        "sprints", "course-enroll", "ai-buildcamp",
        "a@example.com", "b@example.com", "--cohort", "4",
    ])
    assert result.exit_code == 0, result.output
    assert client.calls == [(
        "/api/courses/ai-buildcamp/enrollments",
        {"json_body": {"user_emails": ["a@example.com", "b@example.com"], "cohort": "4"}},
    )]
    assert json.loads(result.output) == RESPONSE


def test_course_enroll_without_cohort_omits_it(client):
    result = CliRunner().invoke(cli, ["sprints", "course-enroll", "ai-buildcamp", "a@example.com"])
    assert result.exit_code == 0, result.output
    assert client.calls == [(
        "/api/courses/ai-buildcamp/enrollments",
        {"json_body": {"user_emails": ["a@example.com"]}},
    )]
