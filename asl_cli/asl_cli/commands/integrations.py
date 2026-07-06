"""``asl integrations`` -- integration settings."""

from __future__ import annotations

import click

from asl_cli.commands._shared import emit, format_option, get_client

API = "/api"


@click.group()
def integrations():
    """Manage integration settings."""


@integrations.command("settings")
@click.option("--group", "group_filter", default=None, help="Filter by setting group.")
@format_option
def integrations_settings(group_filter, fmt):
    """List settings (keys, source, configured -- never values)."""
    params = {}
    if group_filter:
        params["group"] = group_filter
    data = get_client().get(f"{API}/integrations/settings", params=params or None)
    if fmt == "table":
        rows = data.get("settings", []) if isinstance(data, dict) else data
        emit(rows, fmt, columns=["key", "group", "configured", "source"])
    else:
        emit(data, fmt)


@integrations.command("set")
@click.option("--updates", required=True,
              help="Comma-separated key=value pairs, e.g. CONTENT_CDN_BASE=https://cdn.example.com")
@format_option
def integrations_set(updates, fmt):
    """Set integration settings (all-or-nothing batch)."""
    pairs = [kv.strip() for kv in updates.split(",") if kv.strip()]
    update_list = []
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep:
            raise click.BadParameter(f"Expected key=value, got {pair!r}")
        update_list.append({"key": key.strip(), "value": value.strip()})
    emit(get_client().post(f"{API}/integrations/settings", json_body={"updates": update_list}), fmt)


groups = [integrations]
