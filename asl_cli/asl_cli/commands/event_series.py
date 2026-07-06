"""``asl event-series`` -- series CRUD, occurrences, Zoom provisioning."""

from __future__ import annotations

import click

from asl_cli.commands._shared import emit, format_option, get_client, json_arg

API = "/api"

commands = []


@click.command("event-series-list")
@format_option
def event_series_list(fmt):
    """List event series."""
    data = get_client().get(f"{API}/event-series")
    emit(data, fmt)


commands.append(event_series_list)


@click.command("event-series-get")
@click.argument("series_id", type=int)
@format_option
def event_series_get(series_id, fmt):
    """Get a single event series."""
    data = get_client().get(f"{API}/event-series/{series_id}")
    emit(data, fmt)


commands.append(event_series_get)


@click.command("event-series-create")
@json_arg("data", required=True)
@format_option
def event_series_create(data, fmt):
    """Create an event series (JSON body)."""
    result = get_client().post(f"{API}/event-series", json_body=data)
    emit(result, fmt)


commands.append(event_series_create)


@click.command("event-series-update")
@click.argument("series_id", type=int)
@json_arg("data", required=True)
@format_option
def event_series_update(series_id, data, fmt):
    """Update an event series (JSON body)."""
    result = get_client().patch(f"{API}/event-series/{series_id}", json_body=data)
    emit(result, fmt)


commands.append(event_series_update)


@click.command("event-series-occurrences-bulk")
@click.argument("series_id", type=int)
@json_arg("data", required=True)
@format_option
def event_series_occurrences_bulk(series_id, data, fmt):
    """Bulk-add occurrences to a series (JSON body with 'occurrences' list)."""
    result = get_client().post(f"{API}/event-series/{series_id}/occurrences/bulk", json_body=data)
    emit(result, fmt)


commands.append(event_series_occurrences_bulk)


@click.command("event-series-occurrences-reconcile")
@click.argument("series_id", type=int)
@json_arg("data", required=True)
@format_option
def event_series_occurrences_reconcile(series_id, data, fmt):
    """Exact-set occurrences for a series (PUT, JSON body)."""
    result = get_client().put(f"{API}/event-series/{series_id}/occurrences", json_body=data)
    emit(result, fmt)


commands.append(event_series_occurrences_reconcile)


@click.command("event-series-zoom-meetings")
@click.argument("series_id", type=int)
@click.option("--dry-run", is_flag=True, default=False)
@format_option
def event_series_zoom_meetings(series_id, dry_run, fmt):
    """Provision Zoom meetings for eligible occurrences."""
    body = {"dry_run": True} if dry_run else {}
    data = get_client().post(f"{API}/event-series/{series_id}/zoom-meetings", json_body=body)
    emit(data, fmt)


commands.append(event_series_zoom_meetings)
