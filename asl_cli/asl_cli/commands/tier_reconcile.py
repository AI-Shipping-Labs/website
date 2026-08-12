"""Safe operator commands for Stripe subscription reconciliation reports."""

from __future__ import annotations

import copy
import time
from functools import wraps
from typing import Any, Callable

import click
import httpx
from click.core import ParameterSource

from asl_cli.client import APIError
from asl_cli.commands._shared import emit, format_option, get_client, json_option
from asl_cli.output import print_table

API = "/api/payments/tier-reconcile"
RUNS_API = f"{API}/runs"
RUNNING_STATUSES = {"queued", "running"}

DEFAULT_POLL_INTERVAL = 2.0
DEFAULT_TIMEOUT = 15 * 60.0
DEFAULT_LIST_PAGE_SIZE = 100
DEFAULT_DETAIL_PAGE_SIZE = 500

_sleep = time.sleep
_monotonic = time.monotonic

SUMMARY_COLUMNS = [
    "run_id",
    "status",
    "source",
    "started_at",
    "finished_at",
    "cohort_count",
    "in_sync_count",
    "scheduled_cancellation_count",
    "actionable_count",
    "warning_count",
    "changed_count",
]
FINDING_COLUMNS = [
    "user_id",
    "email",
    "website_tier",
    "stripe_status",
    "stripe_tier",
    "cancel_at_period_end",
    "stripe_period_end",
    "classification",
    "action",
    "outcome",
    "message",
    "webhook_event_id",
    "webhook_event_type",
    "webhook_event_status",
    "webhook_processed_at",
    "webhook_evidence",
]
PII_FINDING_COLUMNS = [
    "stripe_customer_id",
    "current_subscription_id",
    "stripe_subscription_id",
]
LIST_COLUMNS = list(SUMMARY_COLUMNS)

groups = []


class WaitTimeout(Exception):
    """Raised when local polling reaches its timeout."""


def _safe_command(func: Callable[..., Any]) -> Callable[..., Any]:
    """Turn API/config/network failures into stable Click exit code 1."""

    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except APIError as exc:
            raise click.ClickException(str(exc)) from exc
        except httpx.HTTPError as exc:
            raise click.ClickException(
                f"API request failed: {exc.__class__.__name__}"
            ) from exc
        except click.exceptions.Exit:
            raise
        except RuntimeError as exc:
            raise click.ClickException(str(exc)) from exc

    return wrapper


def reconcile_format_option(func):
    """Report commands default to a human-readable table."""
    return click.option(
        "-f",
        "--format",
        "fmt",
        type=click.Choice(["table", "json", "raw"]),
        default="table",
        show_default=True,
        help="Output format: table (default), pretty JSON, or compact raw JSON.",
    )(func)


def include_pii_option(func):
    return click.option(
        "--include-pii",
        is_flag=True,
        help=(
            "Reveal member email addresses and Stripe identifiers. Output may "
            "contain PII; store or share it only in an approved location."
        ),
    )(func)


def fail_on_option(func):
    return click.option(
        "--fail-on",
        type=click.Choice(["never", "actionable", "warning", "any"]),
        default="never",
        show_default=True,
        help="Exit 4 after a completed report when the selected threshold exists.",
    )(func)


def report_filter_options(func):
    options = [
        click.option(
            "--filter",
            "report_filter",
            type=click.Choice(["all", "actionable", "scheduled", "warnings"]),
            default="all",
            show_default=True,
            help="Server-owned saved finding filter.",
        ),
        click.option(
            "--tier",
            type=click.Choice(["basic", "main", "premium", "free"]),
            help="Filter by the member's website tier.",
        ),
        click.option(
            "--classification",
            help="Exact server-owned classification value (not validated locally).",
        ),
        click.option(
            "--page",
            type=click.IntRange(min=1),
            help="Fetch only this one findings page instead of all pages.",
        ),
        click.option(
            "--page-size",
            type=click.IntRange(min=1, max=500),
            default=DEFAULT_DETAIL_PAGE_SIZE,
            show_default=True,
        ),
        click.option(
            "--all-pages",
            is_flag=True,
            help="Explicitly fetch all findings pages (already the default).",
        ),
    ]
    for option in reversed(options):
        func = option(func)
    return func


def wait_options(func):
    options = [
        click.option(
            "--poll-interval",
            type=click.FloatRange(min=0, min_open=True),
            default=DEFAULT_POLL_INTERVAL,
            show_default=True,
            metavar="SECONDS",
        ),
        click.option(
            "--timeout",
            type=click.FloatRange(min=0, min_open=True),
            default=DEFAULT_TIMEOUT,
            show_default=True,
            metavar="SECONDS",
        ),
    ]
    for option in reversed(options):
        func = option(func)
    return func


def _explicit_option(ctx: click.Context, name: str) -> bool:
    return ctx.get_parameter_source(name) == ParameterSource.COMMANDLINE


def _validate_page_mode(ctx: click.Context, page: int | None, all_pages: bool):
    if page is not None and all_pages:
        raise click.UsageError("--page cannot be combined with --all-pages")


def _detail_params(
    *,
    report_filter: str,
    tier: str | None,
    classification: str | None,
    page: int,
    page_size: int,
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "filter": report_filter,
        "page": page,
        "page_size": page_size,
    }
    if tier:
        params["tier"] = tier
    if classification:
        params["classification"] = classification
    return params


def _get_detail(
    client,
    run_id: str,
    *,
    report_filter: str,
    tier: str | None,
    classification: str | None,
    page: int,
    page_size: int,
) -> dict[str, Any]:
    return client.get(
        f"{RUNS_API}/{run_id}",
        params=_detail_params(
            report_filter=report_filter,
            tier=tier,
            classification=classification,
            page=page,
            page_size=page_size,
        ),
    )


def _combine_pages(
    client,
    run_id: str,
    first: dict[str, Any],
    *,
    report_filter: str,
    tier: str | None,
    classification: str | None,
    page_size: int,
    one_page: bool,
) -> dict[str, Any]:
    """Follow opaque server cursors and return one combined response document."""
    combined = copy.deepcopy(first)
    findings = list(combined.get("findings") or [])
    pages_fetched = 1
    next_cursor = combined.get("next_cursor")

    while next_cursor is not None and not one_page:
        response = _get_detail(
            client,
            run_id,
            report_filter=report_filter,
            tier=tier,
            classification=classification,
            page=next_cursor,
            page_size=page_size,
        )
        findings.extend(response.get("findings") or [])
        pages_fetched += 1
        next_cursor = response.get("next_cursor")

    combined["findings"] = findings
    combined["next_cursor"] = next_cursor
    combined["pagination"] = {
        "pages_fetched": pages_fetched,
        "page_size": page_size,
        "all_pages": not one_page,
        "next_cursor": next_cursor,
    }
    if combined.get("run", {}).get("status") != "completed":
        # A queued/running/failed report is not final. Keep its current summary
        # and filtered count, but never present partially persisted rows as a
        # complete findings report.
        combined["findings"] = []
        combined["pagination"]["partial_findings_omitted"] = True
    return combined


def _wait_for_terminal(
    client,
    run_id: str,
    *,
    report_filter: str,
    tier: str | None,
    classification: str | None,
    page: int,
    page_size: int,
    poll_interval: float,
    timeout: float,
) -> dict[str, Any]:
    started = _monotonic()
    while True:
        response = _get_detail(
            client,
            run_id,
            report_filter=report_filter,
            tier=tier,
            classification=classification,
            page=page,
            page_size=page_size,
        )
        status = response.get("run", {}).get("status", "unknown")
        if status not in RUNNING_STATUSES:
            return response

        click.echo(f"Run {run_id} is {status}; waiting...", err=True)
        elapsed = _monotonic() - started
        if elapsed >= timeout:
            raise WaitTimeout
        _sleep(min(poll_interval, timeout - elapsed))


def _mask_email(value: Any) -> Any:
    if not isinstance(value, str) or "@" not in value:
        return "[REDACTED]" if value else value
    local, domain = value.rsplit("@", 1)
    first = local[:1]
    return f"{first}***@{domain}"


def _redact_report(data: Any, *, include_pii: bool) -> Any:
    redacted = copy.deepcopy(data)
    if not include_pii:
        _redact_value(redacted)
    if isinstance(redacted, dict):
        redacted["pii_redacted"] = not include_pii
    return redacted


def _redact_value(value: Any):
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "email":
                value[key] = "[REDACTED]" if item else item
            elif key in {
                "stripe_customer_id",
                "current_subscription_id",
                "stripe_subscription_id",
            }:
                value[key] = "[REDACTED]" if item else item
            else:
                _redact_value(item)
    elif isinstance(value, list):
        for item in value:
            _redact_value(item)


def _summary_row(run: dict[str, Any]) -> dict[str, Any]:
    counts = run.get("counts") or {}
    return {
        "run_id": run.get("id", ""),
        "status": run.get("status", ""),
        "source": run.get("source", ""),
        "started_at": run.get("started_at"),
        "finished_at": run.get("finished_at"),
        "cohort_count": counts.get("cohort", 0),
        "in_sync_count": counts.get("ok", 0),
        "scheduled_cancellation_count": counts.get("scheduled_cancellation", 0),
        "actionable_count": counts.get("actionable", 0),
        "warning_count": counts.get("warning", 0),
        "changed_count": counts.get("changed", 0),
    }


def _finding_row(finding: dict[str, Any], *, include_pii: bool) -> dict[str, Any]:
    webhook = finding.get("webhook") or {}
    row = {
        "user_id": finding.get("user_id"),
        "email": (
            finding.get("email")
            if include_pii
            else _mask_email(finding.get("email"))
        ),
        "website_tier": finding.get("current_tier"),
        "stripe_status": finding.get("stripe_status"),
        "stripe_tier": finding.get("stripe_tier"),
        "cancel_at_period_end": finding.get("cancel_at_period_end"),
        "stripe_period_end": finding.get("stripe_period_end"),
        "classification": finding.get("classification"),
        "action": finding.get("action"),
        "outcome": finding.get("outcome"),
        "message": finding.get("message"),
        "webhook_event_id": webhook.get("event_id"),
        "webhook_event_type": webhook.get("event_type"),
        "webhook_event_status": webhook.get("event_status"),
        "webhook_processed_at": webhook.get("processed_at"),
        "webhook_evidence": webhook.get("evidence"),
    }
    if include_pii:
        for column in PII_FINDING_COLUMNS:
            row[column] = finding.get(column)
    return row


def _emit_detail_table(
    report: dict[str, Any],
    *,
    include_pii: bool,
    unfiltered: bool,
):
    run = report.get("run") or {}
    print_table([_summary_row(run)], columns=SUMMARY_COLUMNS)
    status = run.get("status")
    if status in RUNNING_STATUSES:
        click.echo(f"\nRun is {status}; this report is not final.")
        return
    if status == "failed":
        message = run.get("error_message") or "The server-side run failed."
        click.echo(f"\nRun failed: {message}")
        return

    findings = report.get("findings") or []
    if not findings:
        counts = run.get("counts") or {}
        if unfiltered and counts.get("cohort", 0) == counts.get("ok", 0):
            click.echo(
                "\nNo reconciliation findings. "
                f"All {counts.get('ok', 0)} checked members are in sync."
            )
        else:
            click.echo("\nNo reconciliation findings match the selected filters.")
        return

    columns = FINDING_COLUMNS + (PII_FINDING_COLUMNS if include_pii else [])
    rows = [_finding_row(finding, include_pii=include_pii) for finding in findings]
    click.echo()
    print_table(rows, columns=columns)


def _emit_report(
    report: dict[str, Any],
    *,
    fmt: str,
    include_pii: bool,
    unfiltered: bool,
):
    if fmt == "table":
        _emit_detail_table(report, include_pii=include_pii, unfiltered=unfiltered)
    else:
        emit(_redact_report(report, include_pii=include_pii), fmt)


def _emit_run_list(data: dict[str, Any], *, fmt: str, include_pii: bool):
    if fmt == "table":
        rows = [_summary_row(run) for run in data.get("runs") or []]
        if rows:
            print_table(rows, columns=LIST_COLUMNS)
        else:
            click.echo("No reconciliation runs found.")
    else:
        emit(_redact_report(data, include_pii=include_pii), fmt)


def _report_exit_code(report: dict[str, Any], fail_on: str) -> int:
    run = report.get("run") or {}
    status = run.get("status")
    if status == "failed":
        return 1
    if status != "completed" or fail_on == "never":
        return 0
    counts = run.get("counts") or {}
    if fail_on == "actionable" and counts.get("actionable", 0) > 0:
        return 4
    if fail_on == "warning" and counts.get("warning", 0) > 0:
        return 4
    if fail_on == "any" and bool(report.get("findings")):
        return 4
    return 0


def _finish_report(
    ctx: click.Context,
    report: dict[str, Any],
    *,
    fmt: str,
    include_pii: bool,
    fail_on: str,
    unfiltered: bool,
):
    _emit_report(
        report,
        fmt=fmt,
        include_pii=include_pii,
        unfiltered=unfiltered,
    )
    exit_code = _report_exit_code(report, fail_on)
    if exit_code == 1:
        click.echo(
            f"Run {report.get('run', {}).get('id', '')} failed on the server.",
            err=True,
        )
    elif exit_code == 4:
        click.echo(f"--fail-on {fail_on} threshold matched.", err=True)
    if exit_code:
        ctx.exit(exit_code)


def _handle_poll_stop(ctx: click.Context, run_id: str, exc: BaseException):
    if isinstance(exc, WaitTimeout):
        click.echo(
            f"Timed out waiting for run {run_id}; the server-side run continues.\n"
            f"Resume with: asl tier-reconcile wait {run_id}",
            err=True,
        )
        ctx.exit(3)
    click.echo(
        f"Polling stopped for run {run_id}; the server-side run continues.\n"
        f"Resume with: asl tier-reconcile wait {run_id}",
        err=True,
    )
    ctx.exit(130)


@click.group(name="tier-reconcile")
def tier_reconcile():
    """Compare website tiers with Stripe through safe operator APIs."""


@tier_reconcile.command("diagnostics")
@format_option
@_safe_command
def tier_reconcile_diagnostics(fmt):
    """Run the older synchronous, email-targeted read-only diagnostic."""
    emit(get_client().get(f"{API}/diagnostics"), fmt)


@tier_reconcile.command("apply")
@json_option("data", required=True)
@format_option
@_safe_command
def tier_reconcile_apply(data, fmt):
    """Apply explicitly confirmed repairs (guarded write; not a report command)."""
    emit(get_client().post(API, json_body=data), fmt)


@tier_reconcile.command("list")
@click.option("--page", type=click.IntRange(min=1), default=1, show_default=True)
@click.option(
    "--page-size",
    type=click.IntRange(min=1, max=500),
    default=DEFAULT_LIST_PAGE_SIZE,
    show_default=True,
)
@click.option("--all-pages", is_flag=True, help="Follow every next_cursor.")
@include_pii_option
@reconcile_format_option
@click.pass_context
@_safe_command
def tier_reconcile_list(ctx, page, page_size, all_pages, include_pii, fmt):
    """List persisted read-only cohort runs, newest first."""
    if all_pages and _explicit_option(ctx, "page"):
        raise click.UsageError("--page cannot be combined with --all-pages")

    client = get_client()
    response = client.get(RUNS_API, params={"page": page, "page_size": page_size})
    combined = copy.deepcopy(response)
    runs = list(response.get("runs") or [])
    pages_fetched = 1
    next_cursor = response.get("next_cursor")
    while all_pages and next_cursor is not None:
        response = client.get(
            RUNS_API,
            params={"page": next_cursor, "page_size": page_size},
        )
        runs.extend(response.get("runs") or [])
        pages_fetched += 1
        next_cursor = response.get("next_cursor")
    combined["runs"] = runs
    combined["next_cursor"] = next_cursor
    combined["pagination"] = {
        "pages_fetched": pages_fetched,
        "page_size": page_size,
        "all_pages": all_pages,
        "next_cursor": next_cursor,
    }
    _emit_run_list(combined, fmt=fmt, include_pii=include_pii)


@tier_reconcile.command("show")
@click.argument("run_id")
@report_filter_options
@fail_on_option
@include_pii_option
@reconcile_format_option
@click.pass_context
@_safe_command
def tier_reconcile_show(
    ctx,
    run_id,
    report_filter,
    tier,
    classification,
    page,
    page_size,
    all_pages,
    fail_on,
    include_pii,
    fmt,
):
    """Show one persisted read-only run without polling."""
    _validate_page_mode(ctx, page, all_pages)
    requested_page = page or 1
    client = get_client()
    first = _get_detail(
        client,
        run_id,
        report_filter=report_filter,
        tier=tier,
        classification=classification,
        page=requested_page,
        page_size=page_size,
    )
    status = first.get("run", {}).get("status")
    one_page = page is not None or status != "completed"
    report = _combine_pages(
        client,
        run_id,
        first,
        report_filter=report_filter,
        tier=tier,
        classification=classification,
        page_size=page_size,
        one_page=one_page,
    )
    _finish_report(
        ctx,
        report,
        fmt=fmt,
        include_pii=include_pii,
        fail_on=fail_on,
        unfiltered=report_filter == "all" and not tier and not classification,
    )


@tier_reconcile.command("wait")
@click.argument("run_id")
@report_filter_options
@wait_options
@fail_on_option
@include_pii_option
@reconcile_format_option
@click.pass_context
@_safe_command
def tier_reconcile_wait(
    ctx,
    run_id,
    report_filter,
    tier,
    classification,
    page,
    page_size,
    all_pages,
    poll_interval,
    timeout,
    fail_on,
    include_pii,
    fmt,
):
    """Wait for an existing read-only run, then show its complete report."""
    _validate_page_mode(ctx, page, all_pages)
    client = get_client()
    try:
        first = _wait_for_terminal(
            client,
            run_id,
            report_filter=report_filter,
            tier=tier,
            classification=classification,
            page=page or 1,
            page_size=page_size,
            poll_interval=poll_interval,
            timeout=timeout,
        )
    except (WaitTimeout, KeyboardInterrupt) as exc:
        _handle_poll_stop(ctx, run_id, exc)

    report = _combine_pages(
        client,
        run_id,
        first,
        report_filter=report_filter,
        tier=tier,
        classification=classification,
        page_size=page_size,
        one_page=page is not None or first.get("run", {}).get("status") != "completed",
    )
    _finish_report(
        ctx,
        report,
        fmt=fmt,
        include_pii=include_pii,
        fail_on=fail_on,
        unfiltered=report_filter == "all" and not tier and not classification,
    )


@tier_reconcile.command("run")
@click.option(
    "--no-wait",
    is_flag=True,
    help="Return the queued run ID/status immediately without polling.",
)
@report_filter_options
@wait_options
@fail_on_option
@include_pii_option
@reconcile_format_option
@click.pass_context
@_safe_command
def tier_reconcile_run(
    ctx,
    no_wait,
    report_filter,
    tier,
    classification,
    page,
    page_size,
    all_pages,
    poll_interval,
    timeout,
    fail_on,
    include_pii,
    fmt,
):
    """Enqueue one full-cohort read-only diagnostic and wait by default."""
    _validate_page_mode(ctx, page, all_pages)
    client = get_client()
    queued = client.post(RUNS_API)
    run_id = queued.get("run_id", "")
    if no_wait:
        if fmt == "table":
            print_table(
                [{"run_id": run_id, "status": queued.get("status")}],
                columns=["run_id", "status"],
            )
        else:
            emit(_redact_report(queued, include_pii=include_pii), fmt)
        return

    try:
        first = _wait_for_terminal(
            client,
            run_id,
            report_filter=report_filter,
            tier=tier,
            classification=classification,
            page=page or 1,
            page_size=page_size,
            poll_interval=poll_interval,
            timeout=timeout,
        )
    except (WaitTimeout, KeyboardInterrupt) as exc:
        _handle_poll_stop(ctx, run_id, exc)

    report = _combine_pages(
        client,
        run_id,
        first,
        report_filter=report_filter,
        tier=tier,
        classification=classification,
        page_size=page_size,
        one_page=page is not None or first.get("run", {}).get("status") != "completed",
    )
    _finish_report(
        ctx,
        report,
        fmt=fmt,
        include_pii=include_pii,
        fail_on=fail_on,
        unfiltered=report_filter == "all" and not tier and not classification,
    )


groups.append(tier_reconcile)
