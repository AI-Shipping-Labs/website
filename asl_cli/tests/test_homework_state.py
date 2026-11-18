"""``asl homework close`` / ``set-state`` / ``close-project`` / ``set-project-state`` wiring tests."""

from __future__ import annotations

import json

import pytest
from asl_cli.cli import cli
from asl_cli.client import APIError
from asl_cli.commands import homework as homework_module
from click.testing import CliRunner

RESPONSE = {"id": 7, "state": "CL", "previous_state": "OP", "changed": True}


class RecordingClient:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def patch(self, path, **kwargs):
        self.calls.append(("PATCH", path, kwargs))
        if self.error is not None:
            raise self.error
        return RESPONSE


@pytest.fixture
def client(monkeypatch):
    recorder = RecordingClient()
    monkeypatch.setattr(homework_module, "get_client", lambda: recorder)
    return recorder


@pytest.mark.parametrize(("args", "path", "state"), [
    (["homework", "close", "ai-buildcamp", "7"],
     "/api/courses/ai-buildcamp/homework/7", "closed"),
    (["homework", "set-state", "ai-buildcamp", "7", "open"],
     "/api/courses/ai-buildcamp/homework/7", "open"),
    (["homework", "set-state", "ai-buildcamp", "7", "sc"],
     "/api/courses/ai-buildcamp/homework/7", "SC"),
    (["homework", "close-project", "ai-buildcamp", "45"],
     "/api/courses/ai-buildcamp/projects/45", "closed"),
    (["homework", "set-project-state", "ai-buildcamp", "45", "peer_reviewing"],
     "/api/courses/ai-buildcamp/projects/45", "peer_reviewing"),
    (["homework", "set-project-state", "ai-buildcamp", "45", "CO"],
     "/api/courses/ai-buildcamp/projects/45", "CO"),
])
def test_state_commands_patch_state(client, args, path, state):
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 0, result.output
    assert client.calls == [("PATCH", path, {"json_body": {"state": state}})]
    assert json.loads(result.output) == RESPONSE


def test_set_state_rejects_unknown_state_without_calling_api(client):
    result = CliRunner().invoke(cli, ["homework", "set-state", "ai-buildcamp", "7", "done"])
    assert result.exit_code == 2
    assert "Invalid value for 'STATE'" in result.output
    assert client.calls == []


def test_set_project_state_rejects_homework_only_state(client):
    result = CliRunner().invoke(
        cli, ["homework", "set-project-state", "ai-buildcamp", "45", "scored"],
    )
    assert result.exit_code == 2
    assert client.calls == []


def test_homework_id_must_be_integer(client):
    result = CliRunner().invoke(cli, ["homework", "close", "ai-buildcamp", "week-2"])
    assert result.exit_code == 2
    assert client.calls == []


def test_api_error_propagates_to_cli_error_handler(monkeypatch):
    recorder = RecordingClient(error=APIError(
        409, {"error": "Only a project in Peer reviewing can be completed (it is Closed).",
              "code": "invalid_transition"},
        "https://aishippinglabs.com/api/courses/ai-buildcamp/projects/45",
    ))
    monkeypatch.setattr(homework_module, "get_client", lambda: recorder)
    result = CliRunner().invoke(
        cli, ["homework", "set-project-state", "ai-buildcamp", "45", "completed"],
    )
    assert result.exit_code != 0
    assert isinstance(result.exception, APIError)
    assert result.exception.status == 409
    assert result.exception.body["code"] == "invalid_transition"
