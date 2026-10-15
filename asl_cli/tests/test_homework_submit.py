"""``asl homework list`` / ``submit`` / ``submit-project`` wiring tests."""

from __future__ import annotations

import json

import pytest
from asl_cli.cli import cli
from asl_cli.commands import homework as homework_module
from click.testing import CliRunner

RESPONSE = {"kind": "homework", "status": "would_create", "dry_run": True}


class RecordingClient:
    def __init__(self):
        self.calls = []

    def get(self, path, **kwargs):
        self.calls.append(("GET", path, kwargs))
        return {"course": "ai-buildcamp", "homework": []}

    def post(self, path, **kwargs):
        self.calls.append(("POST", path, kwargs))
        return RESPONSE


@pytest.fixture
def client(monkeypatch):
    recorder = RecordingClient()
    monkeypatch.setattr(homework_module, "get_client", lambda: recorder)
    return recorder


def _body(client):
    [(method, path, kwargs)] = client.calls
    assert (method, path) == ("POST", "/api/submissions/on-behalf")
    return kwargs["json_body"]


def test_submit_posts_homework_with_answers_and_safe_defaults(client):
    result = CliRunner().invoke(cli, [
        "homework", "submit", "--as", "member@example.com",
        "--course", "ai-buildcamp", "--homework", "week-2-homework",
        "--answer", "q-model=2", "--answer", "project-idea=A RAG bot",
    ])

    assert result.exit_code == 0, result.output
    assert _body(client) == {
        "kind": "homework",
        "user": "member@example.com",
        "course": "ai-buildcamp",
        "homework": "week-2-homework",
        "answers": {"q-model": "2", "project-idea": "A RAG bot"},
        "replace": False,
        "dry_run": False,
        "notify": False,
    }
    assert json.loads(result.output) == RESPONSE


def test_submit_passes_flags_and_reads_answer_and_note_from_files(client, tmp_path):
    idea = tmp_path / "idea.txt"
    idea.write_text("Line one\nLine two\n")
    result = CliRunner().invoke(cli, [
        "homework", "submit", "--as", "42", "--homework-id", "7",
        "--answer", f"project-idea=@{idea}",
        "--submitted-at", "2026-09-01T09:30:00Z", "--note", "Sent by email",
        "--link", "https://github.com/m/repo", "--lip-link", "https://x.com/p/1",
        "--time-homework", "3.5", "--replace", "--dry-run", "--notify",
    ])

    assert result.exit_code == 0, result.output
    assert _body(client) == {
        "kind": "homework",
        "user": "42",
        "homework_id": 7,
        "answers": {"project-idea": "Line one\nLine two\n"},
        "homework_link": "https://github.com/m/repo",
        "time_spent_homework": 3.5,
        "learning_in_public_links": ["https://x.com/p/1"],
        "submitted_at": "2026-09-01T09:30:00Z",
        "note": "Sent by email",
        "replace": True,
        "dry_run": True,
        "notify": True,
    }


def test_submit_reads_answer_from_stdin(client):
    result = CliRunner().invoke(cli, [
        "homework", "submit", "--as", "m@example.com", "--homework-id", "7",
        "--answer", "project-idea=@-",
    ], input="Pasted email body")

    assert result.exit_code == 0, result.output
    assert _body(client)["answers"] == {"project-idea": "Pasted email body"}


def test_submit_without_target_is_a_usage_error(client):
    result = CliRunner().invoke(cli, ["homework", "submit", "--as", "m@example.com"])
    assert result.exit_code == 2
    assert "--homework-id" in result.output
    assert client.calls == []


def test_submit_rejects_malformed_answer(client):
    result = CliRunner().invoke(cli, [
        "homework", "submit", "--as", "m@example.com", "--homework-id", "7",
        "--answer", "no-equals-sign",
    ])
    assert result.exit_code == 2
    assert "KEY=VALUE" in result.output
    assert client.calls == []


def test_submit_project_posts_project_body(client):
    result = CliRunner().invoke(cli, [
        "homework", "submit-project", "--as", "m@example.com",
        "--course", "ai-buildcamp", "--project", "attempt-1",
        "--url", "https://github.com/m/bot", "--description", "Idea from email",
        "--dry-run",
    ])

    assert result.exit_code == 0, result.output
    assert _body(client) == {
        "kind": "project",
        "user": "m@example.com",
        "course": "ai-buildcamp",
        "project": "attempt-1",
        "project_url": "https://github.com/m/bot",
        "description": "Idea from email",
        "replace": False,
        "dry_run": True,
        "notify": False,
    }


def test_list_gets_course_homework(client):
    result = CliRunner().invoke(cli, ["homework", "list", "ai-buildcamp"])
    assert result.exit_code == 0, result.output
    assert client.calls == [("GET", "/api/courses/ai-buildcamp/homework", {})]
