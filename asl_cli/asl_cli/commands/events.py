"""``asl events`` -- events CRUD and explicit operator actions."""

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


GUEST_SUMMARY_COLUMNS = [
    "event_id",
    "event_slug",
    "event_title",
    "guest_email",
    "registration_id",
    "registration_status",
    "email_status",
    "verified",
]


def _guest_summary(post_result, read_back=None):
    summary = {key: post_result.get(key) for key in GUEST_SUMMARY_COLUMNS[:-1]}
    if read_back is None:
        summary["verified"] = True
        return summary

    identity_matches = all(
        read_back.get(key) == post_result.get(key)
        for key in ("event_id", "event_slug", "event_title", "guest_email", "registration_id")
    )
    state_matches = (
        read_back.get("registration_status") == "registered"
        and read_back.get("email_status")
        == {
            "sent": "sent",
            "already_sent": "sent",
            "failed_retryable": "failed_retryable",
        }.get(post_result.get("email_status"))
    )
    summary["verified"] = identity_matches and state_matches
    return summary


@events.command("invite-guest")
@click.argument("event_id", type=click.IntRange(min=1))
@click.option("--email", required=True, help="Exact guest email address.")
@click.option(
    "--dry-run",
    is_flag=True,
    help="Validate and preview without creating or sending anything.",
)
@format_option
def events_invite_guest(event_id, email, dry_run, fmt):
    """Invite one ordinary guest to one event session by numeric ID.

    Performs GET-before, POST, and GET-after verification. Repeating a
    successful invitation is safe and does not send another calendar email.
    First success reports registered / sent; a repeat reports
    already_registered / already_sent. A failed_retryable delivery exits
    non-zero; run the same command again. verified confirms the read-back
    matched the requested event, guest, registration, and delivery state.
    """
    client = get_client()
    event = client.get(f"{API}/events/id/{event_id}")
    if event.get("id") != event_id:
        raise click.ClickException("Event identity verification failed before write.")

    post_result = client.post(
        f"{API}/events/id/{event_id}/guest-invitations",
        json_body={"email": email, "dry_run": dry_run},
    )
    if (
        post_result.get("event_id") != event_id
        or post_result.get("event_slug") != event.get("slug")
        or post_result.get("event_title") != event.get("title")
    ):
        raise click.ClickException("Guest invitation target verification failed.")

    if dry_run:
        summary = _guest_summary(post_result)
    else:
        registration_id = post_result.get("registration_id")
        if not isinstance(registration_id, int):
            raise click.ClickException("Guest invitation returned no registration ID.")
        read_back = client.get(
            f"{API}/events/id/{event_id}/guest-invitations/{registration_id}",
        )
        summary = _guest_summary(post_result, read_back)
        if not summary["verified"]:
            raise click.ClickException("Guest invitation read-after-write verification failed.")

    emit(summary, fmt, columns=GUEST_SUMMARY_COLUMNS)
    if summary["email_status"] == "failed_retryable":
        raise click.exceptions.Exit(1)


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
    click.option("--event-series", "event_series", default=None,
                 help="Attach to a series by pk or slug. Pass an empty "
                      'string ("") to detach.'),
    json_option(
        "timestamps",
        required=False,
        help_text="JSON array of timestamp rows (or @file.json).",
    ),
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


def _validate_timestamps_option(body):
    if "timestamps" in body and not isinstance(body["timestamps"], list):
        raise click.UsageError("--timestamps must be a JSON array.")


def _normalize_event_series(body):
    """Map ``--event-series ""`` to a JSON null so the API detaches the event.

    A non-empty value (pk or slug) is forwarded verbatim; the API resolves it.
    """
    if body.get("event_series") == "":
        body["event_series"] = None


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
    _validate_timestamps_option(body)
    _normalize_event_series(body)
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
    _validate_timestamps_option(body)
    _normalize_event_series(body)
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


@events.command("sync-zoom")
@click.argument("slug")
@format_option
def events_sync_zoom(slug, fmt):
    """Force-sync stored event state to its existing Zoom meeting."""
    emit(get_client().post(f"{API}/events/{slug}/sync-zoom"), fmt)


@events.command("promote-registrations")
@click.argument("slug")
@format_option
def events_promote_registrations(slug, fmt):
    """Promote an event's registrations to its series.

    For every user registered for the event, sets the standing series
    registration and fans it out across the series' upcoming occurrences so
    every event in the series shares the signups. The event's own
    registrations are left intact. Requires the event to be linked to a series.
    """
    emit(
        get_client().post(
            f"{API}/events/{slug}/promote-registrations-to-series"
        ),
        fmt,
    )


groups = [events]
