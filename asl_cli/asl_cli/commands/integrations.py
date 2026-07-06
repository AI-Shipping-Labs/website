"""``asl integrations`` -- settings list/set, plan-sprints ingest."""

from __future__ import annotations

import click

from asl_cli.commands._shared import emit, format_option, get_client, json_arg

API = "/api"

commands = []


@click.command("integrations-settings-list")
@format_option
def integrations_settings_list(fmt):
    """List integration settings (keys, source, configured -- never values)."""
    data = get_client().get(f"{API}/integrations/settings")
    if fmt == "table":
        rows = data.get("settings", []) if isinstance(data, dict) else data
        emit(rows, fmt, columns=["key", "group", "configured", "source"])
    else:
        emit(data, fmt)


commands.append(integrations_settings_list)


@click.command("integrations-settings-set")
@json_arg("data", required=True)
@format_option
def integrations_settings_set(data, fmt):
    """Set integration settings (JSON body with 'updates' list)."""
    result = get_client().post(f"{API}/integrations/settings", json_body=data)
    emit(result, fmt)


commands.append(integrations_settings_set)


@click.command("integrations-plan-sprints-ingest")
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
    data = get_client().post(f"{API}/integrations/slack/plan-sprints/ingest", json_body=body)
    emit(data, fmt)


commands.append(integrations_plan_sprints_ingest)
