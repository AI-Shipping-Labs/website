"""``asl sprints course-access-grant`` / ``course-access-revoke`` wiring tests."""

from __future__ import annotations

import json

import pytest
from asl_cli.cli import cli
from asl_cli.commands import sprints as sprints_module
from click.testing import CliRunner

GRANT_RESPONSE = {
    "course": "ai-buildcamp",
    "cohort": "1",
    "dry_run": True,
    "granted": 1,
    "already_has_access": 0,
    "user_not_found": 0,
    "malformed": 0,
    "results": [{"email": "a@example.com", "status": "would_grant", "access_type": "granted"}],
}
REVOKE_RESPONSE = {"email": "a@example.com", "status": "revoked"}


class RecordingClient:
    def __init__(self):
        self.calls = []

    def post(self, path, **kwargs):
        self.calls.append(("POST", path, kwargs))
        return GRANT_RESPONSE

    def delete(self, path, **kwargs):
        self.calls.append(("DELETE", path, kwargs))
        return REVOKE_RESPONSE


@pytest.fixture
def client(monkeypatch):
    recorder = RecordingClient()
    monkeypatch.setattr(sprints_module, "get_client", lambda: recorder)
    return recorder


def test_grant_emails_posts_email_list(client):
    result = CliRunner().invoke(cli, [
        "sprints", "course-access-grant", "ai-buildcamp", "a@example.com", "b@example.com",
    ])
    assert result.exit_code == 0, result.output
    assert client.calls == [(
        "POST",
        "/api/courses/ai-buildcamp/access",
        {"json_body": {"emails": ["a@example.com", "b@example.com"], "dry_run": False}},
    )]
    assert json.loads(result.output) == GRANT_RESPONSE


def test_grant_cohort_dry_run_posts_cohort_key(client):
    result = CliRunner().invoke(cli, [
        "sprints", "course-access-grant", "ai-buildcamp", "--cohort", "1", "--dry-run",
    ])
    assert result.exit_code == 0, result.output
    assert client.calls == [(
        "POST",
        "/api/courses/ai-buildcamp/access",
        {"json_body": {"cohort": "1", "dry_run": True}},
    )]


@pytest.mark.parametrize("args", [
    [],
    ["a@example.com", "--cohort", "1"],
])
def test_grant_requires_exactly_one_of_emails_or_cohort(client, args):
    result = CliRunner().invoke(cli, ["sprints", "course-access-grant", "ai-buildcamp", *args])
    assert result.exit_code == 2
    assert client.calls == []


def test_revoke_deletes_email_path(client):
    result = CliRunner().invoke(cli, [
        "sprints", "course-access-revoke", "ai-buildcamp", "a@example.com",
    ])
    assert result.exit_code == 0, result.output
    assert client.calls == [(
        "DELETE", "/api/courses/ai-buildcamp/access/a@example.com", {},
    )]
    assert json.loads(result.output) == REVOKE_RESPONSE
