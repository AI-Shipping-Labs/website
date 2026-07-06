"""``asl events`` -- events CRUD, banner, workshop-ready notify."""

from __future__ import annotations

import click

from asl_cli.commands._shared import (
    TierLevel,
    collect_flags,
    comma_list,
    emit,
    format_option,
    get_client,
)

API = "/api"


@click.group()
def events():
    """Manage events."""


@events.command("list")
@click.option("--status", type=click.Choice(["draft", "upcoming", "completed", "cancelled"]))
@format_option
def events_list(status, fmt):
    """List events."""
    params = {}
    if status:
        params["status"] = status
    data = get_client().get(f"{API}/events", params=params)
    if fmt == "table":
        rows = data if isinstance(data, list) else data.get("events", []) if isinstance(data, dict) else data
        emit(rows, fmt, columns=["slug", "title", "start_datetime", "status", "kind"])
    else:
        emit(data, fmt)


@events.command("get")
@click.argument("slug")
@format_option
def events_get(slug, fmt):
    """Get a single event."""
    emit(get_client().get(f"{API}/events/{slug}"), fmt)


# Common flags for create/update. Defined once as decorators, applied in both.

EVENT_FLAGS = [
    click.option("--title", default=None, help="Event title."),
    click.option("--slug", default=None, help="URL slug (auto-derived from title if omitted)."),
    click.option("--description", default=None, help="Plain-text description."),
    click.option("--kind", type=click.Choice(["standard", "workshop", "meetup", "q_and_a"]), default=None),
    click.option("--platform", type=click.Choice(["zoom", "custom"]), default=None),
    click.option("--start-datetime", default=None, help="ISO 8601 datetime."),
    click.option("--end-datetime", default=None, help="ISO 8601 datetime."),
    click.option("--timezone", default=None, help="IANA timezone, e.g. Europe/Berlin."),
    click.option("--required-level", type=TierLevel(), default=None,
                 help="Access gate: open, registered, basic, main, premium (or integer)."),
    click.option("--status", type=click.Choice(["draft", "upcoming", "completed", "cancelled"]), default=None),
    click.option("--published/--no-published", default=None, help="Publish the event."),
    click.option("--external-host", default=None,
                 help="Partner host: '', Maven, Luma, DataTalksClub."),
    click.option("--host-email", default=None, help="Host attendee email (auto-registers them)."),
    click.option("--host-ids", default=None, help="Comma-separated host profile ids, e.g. 1,2."),
    click.option("--tags", default=None, help="Comma-separated tags, e.g. sprint:may-2026,workshop."),
    click.option("--zoom-join-url", default=None, help="Manual Zoom/custom join URL."),
    click.option("--recording-url", default=None, help="Recording URL."),
    click.option("--create-zoom/--no-create-zoom", default=None,
                 help="Provision a real Zoom meeting (platform must be zoom)."),
    click.option("--generate-banner/--no-generate-banner", default=None,
                 help="Auto-generate the 1200x630 banner image (default true on create)."),
]


def apply_event_flags(func):
    for decorator in reversed(EVENT_FLAGS):
        func = decorator(func)
    return func


@events.command("create")
@apply_event_flags
@format_option
def events_create(fmt, **kwargs):
    """Create an event. Use --help to see all flags."""
    body = collect_flags(click.get_current_context())
    emit(get_client().post(f"{API}/events", json_body=body), fmt)


@events.command("update")
@click.argument("slug")
@apply_event_flags
@format_option
def events_update(slug, fmt, **kwargs):
    """Update an event. Use --help to see all flags."""
    body = collect_flags(click.get_current_context())
    emit(get_client().patch(f"{API}/events/{slug}", json_body=body), fmt)


@events.command("regenerate-banner")
@click.argument("slug")
@format_option
def events_regenerate_banner(slug, fmt):
    """Force-regenerate an event's banner image."""
    emit(get_client().post(f"{API}/events/{slug}/regenerate-banner"), fmt)


@events.command("notify-workshop-ready")
@click.argument("slug")
@format_option
def events_notify_workshop_ready(slug, fmt):
    """Notify that a workshop event is ready."""
    emit(get_client().post(f"{API}/events/{slug}/notify-workshop-ready"), fmt)


groups = [events]
