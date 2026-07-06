"""``asl integrations`` -- settings list/set, plan-sprints ingest."""

from __future__ import annotations

import click

from asl_cli.commands._shared import emit, format_option, get_client

API = "/api"


@click.group()
def integrations():
    """Manage integration settings and triggers."""


@integrations.group("settings")
def integrations_settings():
    """Integration settings (env config framework)."""


@integrations_settings.command("list")
@format_option
def integrations_settings_list(fmt):
    """List settings (keys, source, configured -- never values)."""
    data = get_client().get(f"{API}/integrations/settings")
    if fmt == "table":
        rows = data.get("settings", []) if isinstance(data, dict) else data
        emit(rows, fmt, columns=["key", "group", "configured", "source"])
    else:
        emit(data, fmt)


@integrations_settings.command("set")
@click.option("--updates", required=True,
              help='Comma-separated key=value pairs, e.g. CONTENT_CDN_BASE=https://cdn.example.com')
@format_option
def integrations_settings_set(updates, fmt):
    """Set integration settings (all-or-nothing batch)."""
    pairs = [kv.strip() for kv in updates.split(",") if kv.strip()]
    update_list = []
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep:
            raise click.BadParameter(f"Expected key=value, got {pair!r}")
        update_list.append({"key": key.strip(), "value": value.strip()})
    body = {"updates": update_list}
    emit(get_client().post(f"{API}/integrations/settings", json_body=body), fmt)


@integrations.command("plan-sprints-ingest")
@click.option("--since", default=None, help="ISO timestamp for retroactive backfill.")
@click.option("--dry-run", is_flag=True, default=False)
@format_option
def integrations_plan_sprints_ingest(since, dry_run, fmt):
    """Trigger plan-sprints capture/parse/apply."""
    body: dict = {}
    if since:
        body["since"] = since
    if dry_run:
        body["dry_run"] = True
    emit(get_client().post(f"{API}/integrations/slack/plan-sprints/ingest", json_body=body), fmt)


groups = [integrations]
