"""Offline CLI tests for the cohort reconciliation report (#1414)."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from asl_cli.cli import cli
from asl_cli.client import APIError
from asl_cli.commands import tier_reconcile as command
from click.testing import CliRunner

RUN_ID = "b0a1c2d3-4e5f-6071-8293-a4b5c6d7e8f9"


def run_payload(status="completed", **count_overrides):
    counts = {
        "cohort": 3,
        "ok": 0,
        "scheduled_cancellation": 1,
        "actionable": 1,
        "warning": 1,
        "changed": 0,
    }
    counts.update(count_overrides)
    return {
        "id": RUN_ID,
        "status": status,
        "mode": "diagnostic",
        "source": "api",
        "started_at": "2026-08-12T08:00:00+00:00",
        "finished_at": (
            "2026-08-12T08:01:00+00:00" if status in {"completed", "failed"} else None
        ),
        "error_message": "Stripe authentication failed" if status == "failed" else "",
        "counts": counts,
    }


def finding(
    *,
    email="alexey@example.com",
    status="past_due",
    classification="dunning_grace",
    action="review",
    outcome="warning",
):
    return {
        "email": email,
        "user_id": 1234,
        "current_tier": "main",
        "current_subscription_id": "sub_local",
        "stripe_customer_id": "cus_secret",
        "stripe_subscription_id": "sub_live",
        "stripe_status": status,
        "cancel_at_period_end": False,
        "stripe_period_end": "2026-08-20T00:00:00+00:00",
        "stripe_tier": "main",
        "classification": classification,
        "action": action,
        "outcome": outcome,
        "message": "Exact Stripe state requires operator review.",
        "conflicting_user_ids": [],
        "webhook": {
            "event_id": "evt_123",
            "event_type": "customer.subscription.updated",
            "event_status": "processed",
            "processed_at": "2026-08-12T07:59:00+00:00",
            "evidence": "processed",
        },
    }


def detail_payload(
    status="completed",
    *,
    findings=None,
    next_cursor=None,
    page=1,
    **count_overrides,
):
    rows = [finding()] if findings is None else findings
    return {
        "run": run_payload(status, **count_overrides),
        "webhook_evidence_counts": {
            "processed": len(rows),
            "failed_permanent": 0,
            "missing": 0,
            "not_applicable": 0,
        },
        "count": len(rows),
        "page": page,
        "next_cursor": next_cursor,
        "findings": rows,
    }


class StatefulFakeClient:
    def __init__(self, *, detail_responses=None, list_responses=None, post_response=None):
        self.calls = []
        self.detail_responses = list(detail_responses or [])
        self.list_responses = dict(list_responses or {})
        self.post_response = post_response or {"run_id": RUN_ID, "status": "queued"}

    def post(self, path, *, json_body=None):
        self.calls.append(("POST", path, json_body))
        return self.post_response

    def get(self, path, *, params=None):
        params = dict(params or {})
        self.calls.append(("GET", path, params))
        if path == command.RUNS_API:
            return self.list_responses[params["page"]]
        if not self.detail_responses:
            raise AssertionError("Unexpected detail GET")
        return self.detail_responses.pop(0)


@pytest.fixture
def runner():
    return CliRunner()


def invoke_with_client(monkeypatch, runner, client, args):
    monkeypatch.setattr(command, "get_client", lambda: client)
    return runner.invoke(cli, ["tier-reconcile", *args])


@pytest.mark.parametrize("subcommand", ["run", "list", "show", "wait"])
def test_report_command_help_identifies_read_only(runner, subcommand):
    args = ["tier-reconcile", subcommand, "--help"]
    result = runner.invoke(cli, args)
    assert result.exit_code == 0, result.output
    assert "read-only" in result.output
    if subcommand in {"run", "show", "wait"}:
        assert "--include-pii" in result.output
        assert "approved location" in result.output


def test_apply_and_diagnostics_help_distinguishes_legacy_paths(runner):
    group = runner.invoke(cli, ["tier-reconcile", "--help"])
    apply_help = runner.invoke(cli, ["tier-reconcile", "apply", "--help"])
    diagnostics = runner.invoke(cli, ["tier-reconcile", "diagnostics", "--help"])
    assert group.exit_code == apply_help.exit_code == diagnostics.exit_code == 0
    assert "guarded write" in group.output
    assert "email-targeted read-only" in diagnostics.output
    assert "explicitly confirmed repairs" in apply_help.output


def test_legacy_diagnostics_and_guarded_apply_keep_existing_endpoints(
    monkeypatch, runner,
):
    class LegacyClient:
        def __init__(self):
            self.calls = []

        def get(self, path):
            self.calls.append(("GET", path, {}))
            return {"count": 0}

        def post(self, path, *, json_body=None):
            self.calls.append(("POST", path, json_body))
            return {"dry_run": False, "changed": 0}

    client = LegacyClient()
    monkeypatch.setattr(command, "get_client", lambda: client)

    diagnostics = runner.invoke(
        cli,
        ["tier-reconcile", "diagnostics", "--format", "raw"],
    )
    apply = runner.invoke(
        cli,
        [
            "tier-reconcile",
            "apply",
            "--data",
            '{"dry_run":false,"confirm":"apply_stripe_truth"}',
            "--format",
            "raw",
        ],
    )

    assert diagnostics.exit_code == apply.exit_code == 0
    assert client.calls == [
        ("GET", f"{command.API}/diagnostics", {}),
        (
            "POST",
            command.API,
            {"dry_run": False, "confirm": "apply_stripe_truth"},
        ),
    ]


def test_run_enqueues_once_polls_and_renders_exact_canonical_states(
    monkeypatch, runner,
):
    client = StatefulFakeClient(
        detail_responses=[
            detail_payload("queued", findings=[]),
            detail_payload("running", findings=[]),
            detail_payload(),
        ]
    )
    sleeps = []
    monkeypatch.setattr(command, "_sleep", lambda seconds: sleeps.append(seconds))

    result = invoke_with_client(monkeypatch, runner, client, ["run"])

    assert result.exit_code == 0, result.output
    assert client.calls[0] == ("POST", command.RUNS_API, None)
    assert [call[0] for call in client.calls].count("POST") == 1
    assert len([call for call in client.calls if call[0] == "GET"]) == 3
    assert sleeps == [2.0, 2.0]
    assert "website_tier" in result.stdout
    assert "past_due" in result.stdout
    assert "dunning_grace" in result.stdout
    assert "not paying" not in result.stdout.lower()
    assert "downgraded" not in result.stdout.lower()
    assert "webhook_event_id" in result.stdout
    assert "webhook_event_type" in result.stdout
    assert "webhook_event_status" in result.stdout
    assert "webhook_processed_at" in result.stdout
    assert "a***@example.com" in result.stdout
    assert "cus_secret" not in result.stdout
    assert "sub_live" not in result.stdout
    assert "is queued; waiting" in result.stderr
    assert "is running; waiting" in result.stderr
    assert not any(call[1] == command.API for call in client.calls)


def test_run_no_wait_prints_one_json_document_and_never_polls(monkeypatch, runner):
    client = StatefulFakeClient()
    result = invoke_with_client(
        monkeypatch,
        runner,
        client,
        ["run", "--no-wait", "--format", "raw"],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {
        "run_id": RUN_ID,
        "status": "queued",
        "pii_redacted": True,
    }
    assert client.calls == [("POST", command.RUNS_API, None)]


def test_run_does_not_retry_ambiguous_enqueue_failure(monkeypatch, runner):
    class AmbiguousPostClient:
        def __init__(self):
            self.calls = 0

        def post(self, _path):
            self.calls += 1
            raise httpx.ReadTimeout("response lost after enqueue")

    client = AmbiguousPostClient()
    result = invoke_with_client(monkeypatch, runner, client, ["run"])
    assert result.exit_code == 1
    assert client.calls == 1
    assert "ReadTimeout" in result.stderr
    assert "response lost after enqueue" not in result.output


def test_wait_resumes_existing_run_without_enqueuing(monkeypatch, runner):
    client = StatefulFakeClient(
        detail_responses=[
            detail_payload("running", findings=[]),
            detail_payload(),
        ]
    )
    monkeypatch.setattr(command, "_sleep", lambda _seconds: None)
    result = invoke_with_client(monkeypatch, runner, client, ["wait", RUN_ID])
    assert result.exit_code == 0, result.output
    assert all(call[0] == "GET" for call in client.calls)
    assert len(client.calls) == 2


def test_show_follows_returned_cursor_combines_pages_and_redacts_json(
    monkeypatch, runner,
):
    first = finding(email="first@example.com", status="canceled", classification="ended_subscription_still_entitled")
    second = finding(email="second@example.com", status="active", classification="scheduled_cancellation")
    client = StatefulFakeClient(
        detail_responses=[
            detail_payload(findings=[first], next_cursor="opaque-next"),
            detail_payload(findings=[second], page=9),
        ]
    )
    result = invoke_with_client(
        monkeypatch,
        runner,
        client,
        [
            "show",
            RUN_ID,
            "--tier",
            "main",
            "--filter",
            "actionable",
            "--classification",
            "server_future_value",
            "--page-size",
            "1",
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 0, result.output
    body = json.loads(result.stdout)
    assert len(body["findings"]) == 2
    assert [row["email"] for row in body["findings"]] == ["[REDACTED]"] * 2
    assert body["findings"][0]["stripe_customer_id"] == "[REDACTED]"
    assert body["pii_redacted"] is True
    assert body["pagination"] == {
        "pages_fetched": 2,
        "page_size": 1,
        "all_pages": True,
        "next_cursor": None,
    }
    params = client.calls[0][2]
    assert params == {
        "filter": "actionable",
        "page": 1,
        "page_size": 1,
        "tier": "main",
        "classification": "server_future_value",
    }
    assert client.calls[1][2]["page"] == "opaque-next"


def test_show_explicit_page_is_bounded_and_include_pii_reveals_values(
    monkeypatch, runner,
):
    client = StatefulFakeClient(
        detail_responses=[detail_payload(next_cursor="ignored", page=3)]
    )
    result = invoke_with_client(
        monkeypatch,
        runner,
        client,
        ["show", RUN_ID, "--page", "3", "--include-pii", "--format", "raw"],
    )
    assert result.exit_code == 0, result.output
    body = json.loads(result.stdout)
    assert len(client.calls) == 1
    assert client.calls[0][2]["page"] == 3
    assert body["pii_redacted"] is False
    assert body["findings"][0]["email"] == "alexey@example.com"
    assert body["findings"][0]["stripe_customer_id"] == "cus_secret"
    assert body["pagination"]["pages_fetched"] == 1
    assert body["pagination"]["next_cursor"] == "ignored"


def test_explicit_page_and_all_pages_is_usage_error_before_request(
    monkeypatch, runner,
):
    client = StatefulFakeClient()
    result = invoke_with_client(
        monkeypatch,
        runner,
        client,
        ["show", RUN_ID, "--page", "2", "--all-pages"],
    )
    assert result.exit_code == 2
    assert "--page cannot be combined with --all-pages" in result.output
    assert client.calls == []


@pytest.mark.parametrize(
    "option",
    [
        ["--poll-interval", "0"],
        ["--poll-interval", "-1"],
        ["--timeout", "0"],
        ["--timeout", "-1"],
    ],
)
def test_wait_rejects_non_positive_polling_values_before_request(
    monkeypatch, runner, option,
):
    client = StatefulFakeClient()
    result = invoke_with_client(
        monkeypatch,
        runner,
        client,
        ["wait", RUN_ID, *option],
    )
    assert result.exit_code == 2
    assert client.calls == []


def test_list_all_pages_follows_cursor_and_preserves_server_order(
    monkeypatch, runner,
):
    older = {**run_payload(), "id": "older"}
    newest = {**run_payload(), "id": "newest"}
    client = StatefulFakeClient(
        list_responses={
            1: {
                "count": 2,
                "page": 1,
                "next_cursor": "history-cursor",
                "runs": [newest],
            },
            "history-cursor": {
                "count": 2,
                "page": 7,
                "next_cursor": None,
                "runs": [older],
            },
        }
    )
    result = invoke_with_client(
        monkeypatch,
        runner,
        client,
        ["list", "--all-pages", "--page-size", "1", "--format", "raw"],
    )
    assert result.exit_code == 0, result.output
    body = json.loads(result.stdout)
    assert [run["id"] for run in body["runs"]] == ["newest", "older"]
    assert body["pagination"]["pages_fetched"] == 2
    assert client.calls[1][2]["page"] == "history-cursor"


def test_list_explicit_page_with_all_pages_and_bad_page_size_are_usage_errors(
    monkeypatch, runner,
):
    client = StatefulFakeClient()
    combined = invoke_with_client(
        monkeypatch,
        runner,
        client,
        ["list", "--page", "2", "--all-pages"],
    )
    too_large = invoke_with_client(
        monkeypatch,
        runner,
        client,
        ["list", "--page-size", "501"],
    )
    assert combined.exit_code == 2
    assert too_large.exit_code == 2
    assert client.calls == []


def test_completed_clean_run_has_useful_table_message(monkeypatch, runner):
    client = StatefulFakeClient(
        detail_responses=[
            detail_payload(findings=[], cohort=42, ok=42, scheduled_cancellation=0, actionable=0, warning=0)
        ]
    )
    result = invoke_with_client(monkeypatch, runner, client, ["show", RUN_ID])
    assert result.exit_code == 0, result.output
    assert "All 42 checked members are in sync" in result.stdout
    assert "cohort_count" in result.stdout
    assert "in_sync_count" in result.stdout


def test_queued_show_is_honest_and_does_not_follow_pages(monkeypatch, runner):
    client = StatefulFakeClient(
        detail_responses=[
            detail_payload("queued", findings=[], next_cursor="not-final")
        ]
    )
    result = invoke_with_client(monkeypatch, runner, client, ["show", RUN_ID])
    assert result.exit_code == 0, result.output
    assert "Run is queued; this report is not final" in result.stdout
    assert len(client.calls) == 1


def test_failed_run_prints_server_error_and_exits_one_without_success_rows(
    monkeypatch, runner,
):
    client = StatefulFakeClient(
        detail_responses=[detail_payload("failed", findings=[finding()])]
    )
    result = invoke_with_client(monkeypatch, runner, client, ["show", RUN_ID])
    assert result.exit_code == 1
    assert "Run failed: Stripe authentication failed" in result.stdout
    assert "dunning_grace" not in result.stdout
    assert "failed on the server" in result.stderr


@pytest.mark.parametrize(
    "threshold,expected",
    [("never", 0), ("actionable", 4), ("warning", 4), ("any", 4)],
)
def test_fail_on_threshold_prints_report_before_exit(
    monkeypatch, runner, threshold, expected,
):
    client = StatefulFakeClient(detail_responses=[detail_payload()])
    result = invoke_with_client(
        monkeypatch,
        runner,
        client,
        ["show", RUN_ID, "--fail-on", threshold],
    )
    assert result.exit_code == expected
    assert "dunning_grace" in result.stdout
    if expected == 4:
        assert f"--fail-on {threshold} threshold matched" in result.stderr


def test_wait_timeout_uses_injected_clock_and_prints_resume_command(
    monkeypatch, runner,
):
    client = StatefulFakeClient(
        detail_responses=[
            detail_payload("running", findings=[]),
            detail_payload("running", findings=[]),
        ]
    )
    clock = {"now": 0.0}

    def fake_sleep(seconds):
        clock["now"] += seconds

    monkeypatch.setattr(command, "_sleep", fake_sleep)
    monkeypatch.setattr(command, "_monotonic", lambda: clock["now"])
    result = invoke_with_client(
        monkeypatch,
        runner,
        client,
        ["wait", RUN_ID, "--poll-interval", "1", "--timeout", "1"],
    )
    assert result.exit_code == 3
    assert "server-side run continues" in result.stderr
    assert f"asl tier-reconcile wait {RUN_ID}" in result.stderr


def test_keyboard_interrupt_stops_only_local_polling(monkeypatch, runner):
    client = StatefulFakeClient(
        detail_responses=[detail_payload("running", findings=[])]
    )

    def interrupt(_seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(command, "_sleep", interrupt)
    result = invoke_with_client(monkeypatch, runner, client, ["wait", RUN_ID])
    assert result.exit_code == 130
    assert "Polling stopped" in result.stderr
    assert "server-side run continues" in result.stderr
    assert all(call[0] == "GET" for call in client.calls)


def test_api_error_is_exit_one_and_does_not_expose_auth_headers(monkeypatch, runner):
    class ErrorClient:
        def get(self, _path, **_kwargs):
            raise APIError(404, {"error": "Run not found", "code": "not_found"}, "safe")

    result = invoke_with_client(
        monkeypatch,
        runner,
        ErrorClient(),
        ["show", RUN_ID],
    )
    assert result.exit_code == 1
    assert "Run not found" in result.stderr
    assert "Authorization" not in result.output
    assert "Token " not in result.output


def test_server_owns_unknown_classification_validation(monkeypatch, runner):
    class ValidationClient:
        def __init__(self):
            self.params = None

        def get(self, _path, *, params):
            self.params = params
            raise APIError(
                422,
                {
                    "error": "Unknown classification filter",
                    "code": "validation_error",
                    "details": {"field": "classification"},
                },
                "safe",
            )

    client = ValidationClient()
    result = invoke_with_client(
        monkeypatch,
        runner,
        client,
        ["show", RUN_ID, "--classification", "future-unknown"],
    )
    assert result.exit_code == 1
    assert client.params["classification"] == "future-unknown"
    assert "Unknown classification filter" in result.stderr


def test_legacy_script_is_read_only_deprecation_shim():
    script = (
        Path(__file__).resolve().parents[2] / "scripts" / "tier_reconcile_prod.sh"
    ).read_text(encoding="utf-8")
    assert "deprecated" in script.lower()
    assert 'exec uv run asl tier-reconcile run "$@"' in script
    forbidden = [
        "curl",
        "API_TOKEN",
        "Authorization:",
        "apply_stripe_truth",
        "/api/payments/tier-reconcile",
        "read -r",
    ]
    for value in forbidden:
        assert value not in script
