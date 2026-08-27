"""``asl plans send-ready`` -- single-plan ready delivery (issue #1455)."""

from __future__ import annotations

import json

from asl_cli.cli import cli
from asl_cli.commands import plans as plans_module
from click.testing import CliRunner


def _result(status, *, dry_run=False, sent=False, sent_at=None, error=""):
    return {
        "plan_id": 116,
        "member_id": 42,
        "member_email": "member@example.com",
        "sprint_slug": "august-2026",
        "shared_at": sent_at,
        "ready_email": {
            "dry_run": dry_run,
            "status": status,
            "eligible": status == "eligible",
            "requested": not dry_run,
            "sent": sent,
            "skipped_already_sent": status == "already_sent",
            "skipped_already_shared": status == "already_shared",
            "failed": status == "failed_retryable",
            "retryable": status == "failed_retryable",
            "sent_at": sent_at,
            "error": error,
        },
    }


class RecordingPlansClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def post(self, path, *, json_body=None):
        self.calls.append(("POST", path, json_body))
        return self.response


def _run(monkeypatch, response, args):
    client = RecordingPlansClient(response)
    monkeypatch.setattr(plans_module, "get_client", lambda: client)
    return client, CliRunner().invoke(cli, args)


def test_send_ready_posts_to_the_single_plan_endpoint(monkeypatch):
    client, result = _run(
        monkeypatch,
        _result("sent", sent=True, sent_at="2026-08-14T12:56:44+00:00"),
        ["plans", "send-ready", "116"],
    )

    assert result.exit_code == 0, result.output
    assert client.calls == [("POST", "/api/plans/116/send-ready-email", {})]
    payload = json.loads(result.output)
    assert payload["plan_id"] == 116
    assert payload["ready_email"]["status"] == "sent"


def test_dry_run_sends_dry_run_flag_and_reports_eligible(monkeypatch):
    client, result = _run(
        monkeypatch,
        _result("eligible", dry_run=True),
        ["plans", "send-ready", "116", "--dry-run"],
    )

    assert result.exit_code == 0, result.output
    assert client.calls == [
        ("POST", "/api/plans/116/send-ready-email", {"dry_run": True}),
    ]
    assert json.loads(result.output)["ready_email"]["status"] == "eligible"


def test_already_sent_is_a_successful_terminal_outcome(monkeypatch):
    _client, result = _run(
        monkeypatch,
        _result("already_sent", sent_at="2026-08-14T12:56:44+00:00"),
        ["plans", "send-ready", "116"],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["ready_email"]["status"] == "already_sent"


def test_already_shared_is_a_successful_terminal_outcome(monkeypatch):
    _client, result = _run(
        monkeypatch,
        _result("already_shared"),
        ["plans", "send-ready", "116"],
    )

    assert result.exit_code == 0, result.output
    assert (
        json.loads(result.output)["ready_email"]["status"] == "already_shared"
    )


def test_failed_retryable_prints_result_and_exits_non_zero(monkeypatch):
    _client, result = _run(
        monkeypatch,
        _result("failed_retryable", error="ses exploded"),
        ["plans", "send-ready", "116"],
    )

    assert result.exit_code == 1
    assert '"status": "failed_retryable"' in result.output
    assert "still not shared" in result.output
    assert "ses exploded" not in result.output


def test_table_format_prints_one_plan_scoped_row(monkeypatch):
    _client, result = _run(
        monkeypatch,
        _result("sent", sent=True, sent_at="2026-08-14T12:56:44+00:00"),
        ["plans", "send-ready", "116", "--format", "table"],
    )

    assert result.exit_code == 0, result.output
    header = result.output.splitlines()[0]
    assert "plan_id" in header
    assert "status" in header
    assert "member@example.com" in result.output
    # Plan-scoped output must never imply a whole-sprint bulk run.
    assert "total_plans" not in result.output


def test_raw_format_emits_compact_json(monkeypatch):
    _client, result = _run(
        monkeypatch,
        _result("sent", sent=True),
        ["plans", "send-ready", "116", "--format", "raw"],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["ready_email"]["sent"] is True


def test_help_documents_idempotency_and_reshare_boundary():
    result = CliRunner().invoke(cli, ["plans", "send-ready", "--help"])

    assert result.exit_code == 0
    assert "--dry-run" in result.output
    assert "Re-share" in result.output
    assert "--force" not in result.output
    assert "--resend" not in result.output
