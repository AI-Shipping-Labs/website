"""``asl events`` -- events CRUD, banner, workshop-ready notify."""

from __future__ import annotations

import click

from asl_cli.commands._shared import (
    TierLevel,
    TIER_HELP,
    collect_flags,
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
    data = get_client().get(f"{API}/events", params=params or None)
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


EVENT_FLAGS = [
    click.option("--title", default=None),
    click.option("--slug", default=None, help="URL slug (auto-derived from title if omitted)."),
    click.option("--description", default=None),
    click.option("--kind", type=click.Choice(["standard", "workshop", "meetup", "q_and_a"]), default=None),
    click.option("--platform", type=click.Choice(["zoom", "custom"]), default=None),
    click.option("--start-datetime", default=None, help="ISO 8601 datetime."),
    click.option("--end-datetime", default=None, help="ISO 8601 datetime."),
    click.option("--timezone", default=None, help="IANA timezone, e.g. Europe/Berlin."),
    click.option("--required-level", type=TierLevel(), default=None, help=TIER_HELP),
    click.option("--status", type=click.Choice(["draft", "upcoming", "completed", "cancelled"]),
                 default=None, help="Default: upcoming for create."),
    click.option("--external-host", default=None),
    click.option("--host-email", default=None, help="Auto-registers this user as host attendee."),
    click.option("--host-ids", default=None, help="Comma-separated host profile ids, e.g. 1,2."),
    click.option("--tags", default=None, help="Comma-separated tags, e.g. sprint:may-2026,workshop."),
    click.option("--zoom-join-url", default=None),
    click.option("--recording-url", default=None),
    click.option("--create-zoom/--no-create-zoom", default=None,
                 help="Provision a real Zoom meeting."),
    click.option("--generate-banner/--no-generate-banner", default=None,
                 help="Auto-generate 1200x630 banner (default true on create)."),
    click.option("--publish/--no-publish", "published", default=None,
                 help="Publish the event (default: true for create)."),
]


def apply_event_flags(func):
    for decorator in reversed(EVENT_FLAGS):
        func = decorator(func)
    return func


def _split_csv(value):
    if value is None:
        return None
    return [v.strip() for v in value.split(",") if v.strip()]


def _split_csv_int(value):
    if value is None:
        return None
    return [int(v.strip()) for v in value.split(",") if v.strip()]


@events.command("create")
@apply_event_flags
@format_option
def events_create(fmt, **kwargs):
    """Create an event. Defaults: status=upcoming, published=true."""
    body = collect_flags(click.get_current_context())
    # CLI-level defaults for create (the model defaults to draft).
    body.setdefault("status", "upcoming")
    body.setdefault("published", True)
    # Split comma-separated list flags.
    if "tags" in body and isinstance(body["tags"], str):
        body["tags"] = _split_csv(body["tags"])
    if "host_ids" in body and isinstance(body["host_ids"], str):
        body["host_ids"] = _split_csv_int(body["host_ids"])
    emit(get_client().post(f"{API}/events", json_body=body), fmt)


@events.command("update")
@click.argument("slug")
@apply_event_flags
@format_option
def events_update(slug, fmt, **kwargs):
    """Update an event. Only flags you pass are sent."""
    body = collect_flags(click.get_current_context())
    if "tags" in body and isinstance(body["tags"], str):
        body["tags"] = _split_csv(body["tags"])
    if "host_ids" in body and isinstance(body["host_ids"], str):
        body["host_ids"] = _split_csv_int(body["host_ids"])
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
