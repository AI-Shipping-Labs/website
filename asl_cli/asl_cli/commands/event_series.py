"""``asl event-series`` -- series CRUD, occurrences, Zoom."""

from __future__ import annotations

import click

from asl_cli.commands._shared import (
    TIER_HELP,
    TierLevel,
    collect_flags,
    emit,
    format_option,
    get_client,
    json_option,
)

API = "/api"


@click.group(name="event-series")
def event_series():
    """Manage event series."""


@event_series.command("list")
@click.option("--is-active", type=click.Choice(["true", "false"]), default=None)
@click.option("-q", "--query", default=None)
@format_option
def event_series_list(is_active, query, fmt):
    """List event series."""
    params = {}
    if is_active:
        params["is_active"] = is_active
    if query:
        params["q"] = query
    emit(get_client().get(f"{API}/event-series", params=params or None), fmt)


@event_series.command("get")
@click.argument("series_id", type=int)
@format_option
def event_series_get(series_id, fmt):
    """Get a single event series with occurrences."""
    emit(get_client().get(f"{API}/event-series/{series_id}"), fmt)


SERIES_FLAGS = [
    click.option("--name", default=None),
    click.option("--slug", default=None),
    click.option("--description", default=None),
    click.option("--cadence", type=click.Choice(["weekly"]), default=None),
    click.option("--day-of-week", type=click.IntRange(0, 6), default=None, help="0=Mon..6=Sun."),
    click.option("--start-time", default=None, help="HH:MM or HH:MM:SS."),
    click.option("--timezone", default=None, help="IANA timezone."),
    click.option("--required-level", type=TierLevel(), default=None, help=TIER_HELP),
    click.option("--is-active/--no-is-active", default=None),
]


def apply_series_flags(func):
    for decorator in reversed(SERIES_FLAGS):
        func = decorator(func)
    return func


@event_series.command("create")
@apply_series_flags
@format_option
def event_series_create(fmt, **kwargs):
    """Create an event series."""
    body = collect_flags(click.get_current_context())
    emit(get_client().post(f"{API}/event-series", json_body=body), fmt)


@event_series.command("update")
@click.argument("series_id", type=int)
@apply_series_flags
@format_option
def event_series_update(series_id, fmt, **kwargs):
    """Update an event series."""
    body = collect_flags(click.get_current_context())
    emit(get_client().patch(f"{API}/event-series/{series_id}", json_body=body), fmt)


@event_series.command("add-occurrences")
@click.argument("series_id", type=int)
@json_option("data", required=True,
             help_text='JSON {"occurrences":[{"start_datetime":"..."}]}')
@format_option
def event_series_add_occurrences(series_id, data, fmt):
    """Bulk-add occurrences (additive, never deletes)."""
    emit(get_client().post(f"{API}/event-series/{series_id}/occurrences/bulk", json_body=data), fmt)


@event_series.command("set-occurrences")
@click.argument("series_id", type=int)
@json_option("data", required=True,
             help_text="JSON with full desired occurrence set (exact-set, atomic).")
@format_option
def event_series_set_occurrences(series_id, data, fmt):
    """Exact-set occurrences (creates missing, cancels extras)."""
    emit(get_client().put(f"{API}/event-series/{series_id}/occurrences", json_body=data), fmt)


@event_series.command("create-zoom")
@click.argument("series_id", type=int)
@click.option("--dry-run", is_flag=True, default=False)
@format_option
def event_series_create_zoom(series_id, dry_run, fmt):
    """Provision Zoom meetings for eligible occurrences."""
    body = {"dry_run": True} if dry_run else {}
    emit(get_client().post(f"{API}/event-series/{series_id}/zoom-meetings", json_body=body), fmt)


groups = [event_series]
