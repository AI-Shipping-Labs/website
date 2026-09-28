"""``asl content get`` wiring tests (issue #1834)."""

from __future__ import annotations

import json

import pytest
from asl_cli.cli import cli
from asl_cli.commands import content as content_module
from click.testing import CliRunner

CONTENT_ID = "7c9e6679-7425-40de-944b-e07fc1f90ae7"
UNIT_HTML = '<h2 id="setup">Setup</h2>\n<p>Install uv.</p>'

UNIT_PAYLOAD = {
    "content_id": CONTENT_ID,
    "type": "course_unit",
    "id": 5,
    "title": "Setup",
    "url": "https://aishippinglabs.com/courses/c/m/setup",
    "is_public": False,
    "source": {"commit": "abc1234567890abcdef1234567890abcdef12345"},
    "updated_at": None,
    "markdown": "## Setup\n\nInstall uv.",
    "html": UNIT_HTML,
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
    recorder = RecordingClient(dict(UNIT_PAYLOAD))
    monkeypatch.setattr(content_module, "get_client", lambda: recorder)
    return recorder


def test_get_prints_json_payload(client):
    result = CliRunner().invoke(cli, ["content", "get", CONTENT_ID])
    assert result.exit_code == 0, result.output
    assert client.calls == [(f"/api/content/{CONTENT_ID}", {})]
    assert json.loads(result.output) == UNIT_PAYLOAD


def test_no_body_sends_include_body_false(client):
    result = CliRunner().invoke(cli, ["content", "get", CONTENT_ID, "--no-body"])
    assert result.exit_code == 0, result.output
    assert client.calls == [
        (f"/api/content/{CONTENT_ID}", {"params": {"include_body": "false"}}),
    ]
    assert json.loads(result.output)["type"] == "course_unit"


def test_body_html_writes_raw_string(client):
    result = CliRunner().invoke(cli, ["content", "get", CONTENT_ID, "--body", "html"])
    assert result.exit_code == 0, result.output
    assert result.output == UNIT_HTML + "\n"
    assert client.calls == [(f"/api/content/{CONTENT_ID}", {})]


def test_body_markdown_writes_raw_string_and_ignores_format(client):
    result = CliRunner().invoke(
        cli, ["content", "get", CONTENT_ID, "--body", "markdown", "-f", "table"],
    )
    assert result.exit_code == 0, result.output
    assert result.output == "## Setup\n\nInstall uv.\n"


def test_body_html_on_download_fails(client):
    client.payload = {"type": "download", "markdown": "text", "html": None}
    result = CliRunner().invoke(cli, ["content", "get", CONTENT_ID, "--body", "html"])
    assert result.exit_code != 0
    assert "No stored HTML for this content type (download)" in result.output


def test_body_and_no_body_is_usage_error(client):
    result = CliRunner().invoke(
        cli, ["content", "get", CONTENT_ID, "--no-body", "--body", "html"],
    )
    assert result.exit_code == 2
    assert "--body and --no-body" in result.output
    assert client.calls == []


def test_invalid_uuid_makes_no_request(client):
    result = CliRunner().invoke(cli, ["content", "get", "intro-lesson"])
    assert result.exit_code != 0
    assert "Invalid content_id: intro-lesson (expected a UUID)" in result.output
    assert client.calls == []


def test_table_format_shows_summary_without_bodies(client):
    result = CliRunner().invoke(cli, ["content", "get", CONTENT_ID, "-f", "table"])
    assert result.exit_code == 0, result.output
    header, _separator, row = result.output.splitlines()
    assert header.split() == ["type", "title", "url", "is_public", "commit", "updated_at"]
    assert row.split()[:5] == [
        "course_unit", "Setup", UNIT_PAYLOAD["url"], "false", "abc1234",
    ]
    assert "Install uv" not in result.output


@pytest.mark.parametrize("args", [["content", "--help"], ["content", "get", "--help"]])
def test_help_exits_zero(args):
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 0, result.output
