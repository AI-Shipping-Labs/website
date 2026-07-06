"""``asl events`` -- events CRUD, banner, workshop-ready notify."""

from __future__ import annotations

import click

from asl_cli.commands._shared import emit, format_option, get_client, json_arg

API = "/api"

commands = []


@click.command("events-list")
@click.option("--status", default=None, type=click.Choice(["draft", "upcoming", "completed", "cancelled"]))
@format_option
def events_list(status, fmt):
    """List events."""
    params = {}
    if status:
        params["status"] = status
    data = get_client().get(f"{API}/events", params=params)
    rows = data if isinstance(data, list) else data.get("events", []) if isinstance(data, dict) else data
    if fmt == "table":
        emit(rows, fmt, columns=["slug", "title", "start_datetime", "status", "kind"])
    else:
        emit(data, fmt)


commands.append(events_list)


@click.command("events-get")
@click.argument("slug")
@format_option
def events_get(slug, fmt):
    """Get a single event."""
    data = get_client().get(f"{API}/events/{slug}")
    emit(data, fmt)


commands.append(events_get)


@click.command("events-create")
@json_arg("data", required=True)
@format_option
def events_create(data, fmt):
    """Create an event (JSON body)."""
    result = get_client().post(f"{API}/events", json_body=data)
    emit(result, fmt)


commands.append(events_create)


@click.command("events-update")
@click.argument("slug")
@json_arg("data", required=True)
@format_option
def events_update(slug, data, fmt):
    """Update an event (JSON body)."""
    result = get_client().patch(f"{API}/events/{slug}", json_body=data)
    emit(result, fmt)


commands.append(events_update)


@click.command("events-regenerate-banner")
@click.argument("slug")
@format_option
def events_regenerate_banner(slug, fmt):
    """Force-regenerate an event's banner image."""
    data = get_client().post(f"{API}/events/{slug}/regenerate-banner")
    emit(data, fmt)


commands.append(events_regenerate_banner)


@click.command("events-notify-workshop-ready")
@click.argument("slug")
@format_option
def events_notify_workshop_ready(slug, fmt):
    """Notify that a workshop event is ready."""
    data = get_client().post(f"{API}/events/{slug}/notify-workshop-ready")
    emit(data, fmt)


commands.append(events_notify_workshop_ready)
