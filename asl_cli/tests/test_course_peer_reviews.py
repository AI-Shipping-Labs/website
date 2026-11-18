"""``asl sprints course-peer-reviews`` and ``course-peer-review-assign`` wiring tests."""

from __future__ import annotations

import json

import pytest
from asl_cli.cli import cli
from asl_cli.commands import sprints as sprints_module
from click.testing import CliRunner

RESPONSE = {"course_id": 7, "course": "ai-buildcamp", "projects": []}


class RecordingClient:
    def __init__(self):
        self.calls = []

    def get(self, path, **kwargs):
        self.calls.append(("GET", path, kwargs))
        return RESPONSE

    def post(self, path, **kwargs):
        self.calls.append(("POST", path, kwargs))
        return RESPONSE


@pytest.fixture
def client(monkeypatch):
    recorder = RecordingClient()
    monkeypatch.setattr(sprints_module, "get_client", lambda: recorder)
    return recorder


def test_course_peer_reviews_passes_filters(client):
    result = CliRunner().invoke(cli, [
        "sprints", "course-peer-reviews", "ai-buildcamp",
        "--cohort", "4", "--project", "attempt-1",
    ])
    assert result.exit_code == 0, result.output
    assert client.calls == [(
        "GET", "/api/courses/ai-buildcamp/peer-reviews",
        {"params": {"cohort": "4", "project": "attempt-1"}},
    )]
    assert json.loads(result.output) == RESPONSE


def test_course_peer_review_assign_posts_scope(client):
    result = CliRunner().invoke(cli, [
        "sprints", "course-peer-review-assign", "ai-buildcamp",
        "--cohort", "4", "--project", "attempt-1",
    ])
    assert result.exit_code == 0, result.output
    assert client.calls == [(
        "POST", "/api/courses/ai-buildcamp/peer-reviews/assignments",
        {"json_body": {"cohort": "4", "project": "attempt-1"}},
    )]
    assert json.loads(result.output) == RESPONSE


def test_course_peer_review_assign_without_scope_posts_empty_body(client):
    result = CliRunner().invoke(cli, [
        "sprints", "course-peer-review-assign", "ai-buildcamp",
    ])
    assert result.exit_code == 0, result.output
    assert client.calls == [(
        "POST", "/api/courses/ai-buildcamp/peer-reviews/assignments",
        {"json_body": {}},
    )]

def test_course_peer_review_assign_help_mentions_emails():
    result = CliRunner().invoke(
        cli, ["sprints", "course-peer-review-assign", "--help"],
    )
    assert result.exit_code == 0, result.output
    assert "review-assigned emails" in " ".join(result.output.split())
