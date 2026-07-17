"""``asl sync`` -- content sync sources, triggers, Slack plan-sprints ingest."""

from __future__ import annotations

import click

from asl_cli.commands._shared import emit, format_option, get_client

API = "/api"


@click.group()
def sync():
    """Manage content sync and ingestion."""


@sync.command("sources")
@format_option
def sync_sources(fmt):
    """List registered content sync sources."""
    emit(get_client().get(f"{API}/sync/sources"), fmt)


@sync.command("trigger")
@click.argument("source_id")
@format_option
def sync_trigger(source_id, fmt):
    """Trigger a content sync for one source."""
    emit(get_client().post(f"{API}/sync/sources/{source_id}/trigger"), fmt)


@sync.command("history")
@click.option("--source", default=None, help="Filter by content source UUID.")
@click.option(
    "--status",
    type=click.Choice(["queued", "running", "failed", "partial", "success", "skipped"]),
    default=None,
)
@click.option("--page", type=click.IntRange(min=1), default=1, show_default=True)
@click.option("--page-size", type=click.IntRange(min=1), default=50, show_default=True)
@format_option
def sync_history(source, status, page, page_size, fmt):
    """List logical content sync history."""
    params = {"page": page, "page_size": page_size}
    if source:
        params["source"] = source
    if status:
        params["status"] = status
    emit(get_client().get(f"{API}/sync/history", params=params), fmt)


@sync.command("history-detail")
@click.argument("history_id")
@format_option
def sync_history_detail(history_id, fmt):
    """Show one logical content sync history item."""
    emit(get_client().get(f"{API}/sync/history/{history_id}"), fmt)


@sync.command("plan-sprints")
@click.option("--since", default=None, help="ISO timestamp for retroactive backfill.")
@click.option("--dry-run", is_flag=True, default=False)
@format_option
def sync_plan_sprints(since, dry_run, fmt):
    """Trigger Slack plan-sprints capture/parse/apply."""
    body: dict = {}
    if since:
        body["since"] = since
    if dry_run:
        body["dry_run"] = True
    emit(get_client().post(f"{API}/integrations/slack/plan-sprints/ingest", json_body=body), fmt)


groups = [sync]
