"""``asl integrations`` -- integration settings."""

from __future__ import annotations

import click

from asl_cli.commands._shared import emit, format_option, get_client

API = "/api/v1"
SETTING_FIELDS = ("key", "group", "configured", "source")


def _setting_summary(entry):
    summary = {}
    for field in SETTING_FIELDS:
        summary[field] = entry.get(field)
    return summary


def _filter_settings(settings, group_filter):
    if not group_filter:
        return settings
    filtered = []
    for entry in settings:
        if entry.get("group") == group_filter:
            filtered.append(entry)
    return filtered


def _settings_output(data, group_filter):
    settings = list(data.get("settings", []))
    total = data.get("pagination", {}).get("total", len(settings))
    client = get_client()
    while len(settings) < total:
        page = client.get(
            f"{API}/settings", params={"limit": 100, "offset": len(settings)},
        )
        if not isinstance(page, dict) or not page.get("settings"):
            break
        settings.extend(page["settings"])
    settings = _filter_settings(settings, group_filter)
    summaries = [_setting_summary(entry) for entry in settings]
    return {**data, "settings": summaries}


@click.group()
def integrations():
    """Manage integration settings."""


@integrations.command("settings")
@click.option("--group", "group_filter", default=None, help="Filter by setting group.")
@format_option
def integrations_settings(group_filter, fmt):
    """List settings (keys, source, configured -- never values)."""
    # The package API is paginated; collect all pages so the CLI preserves
    # the old command's complete inventory before applying its group filter.
    data = get_client().get(f"{API}/settings", params={"limit": 100, "offset": 0})
    if isinstance(data, dict):
        data = _settings_output(data, group_filter)
    if fmt == "table":
        rows = data
        if isinstance(data, dict):
            rows = data.get("settings", [])
        emit(rows, fmt, columns=list(SETTING_FIELDS))
        return
    emit(data, fmt, columns=list(SETTING_FIELDS))


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
    values = {item["key"]: item["value"] for item in update_list}
    emit(
        get_client().post(
            f"{API}/settings/import",
            json_body={"settings": values, "reason": "Updated through asl integrations set"},
        ),
        fmt,
    )


groups = [integrations]
