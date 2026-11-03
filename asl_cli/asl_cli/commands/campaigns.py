"""``asl campaigns`` -- email campaign CRUD and dispatch actions."""

from __future__ import annotations

from datetime import datetime

import click

from asl_cli.commands._shared import (
    TIER_HELP,
    TierLevel,
    collect_flags,
    emit,
    format_option,
    get_client,
)

API = "/api"


@click.group()
def campaigns():
    """Manage email campaigns."""


@campaigns.command("list")
@click.option("--status", default=None)
@click.option("--archived", type=click.Choice(["true", "false"]), default=None)
@format_option
def campaigns_list(status, archived, fmt):
    """List email campaigns."""
    params = {}
    if status:
        params["status"] = status
    if archived:
        params["archived"] = archived
    emit(get_client().get(f"{API}/campaigns", params=params or None), fmt)


@campaigns.command("get")
@click.argument("campaign_id", type=int)
@format_option
def campaigns_get(campaign_id, fmt):
    """Get a single campaign."""
    emit(get_client().get(f"{API}/campaigns/{campaign_id}"), fmt)


CAMPAIGN_FLAGS = [
    click.option("--subject", default=None),
    click.option("--body", default=None),
    click.option("--target-min-level", type=TierLevel(), default=None, help=TIER_HELP),
    click.option("--target-tags-any", default=None, help="Comma-separated tags."),
    click.option("--target-tags-none", default=None, help="Comma-separated tags."),
    click.option("--slack-filter", default=None),
    click.option("--audience-verification", default=None),
    click.option(
        "--verify-deadline-days", type=int, default=None,
        help="Verify-or-delete: days before unverified accounts are deleted.",
    ),
    click.option("--target-event", type=int, default=None, help="Event id."),
    click.option("--is-archived/--no-is-archived", default=None),
]


def apply_campaign_flags(func):
    for decorator in reversed(CAMPAIGN_FLAGS):
        func = decorator(func)
    return func


@campaigns.command("create")
@apply_campaign_flags
@format_option
def campaigns_create(fmt, **kwargs):
    """Create a campaign draft."""
    body = collect_flags(click.get_current_context())
    for key in ("target_tags_any", "target_tags_none"):
        if key in body and isinstance(body[key], str):
            body[key] = [t.strip() for t in body[key].split(",") if t.strip()]
    emit(get_client().post(f"{API}/campaigns", json_body=body), fmt)


@campaigns.command("update")
@click.argument("campaign_id", type=int)
@apply_campaign_flags
@format_option
def campaigns_update(campaign_id, fmt, **kwargs):
    """Update a campaign."""
    body = collect_flags(click.get_current_context())
    for key in ("target_tags_any", "target_tags_none"):
        if key in body and isinstance(body[key], str):
            body[key] = [t.strip() for t in body[key].split(",") if t.strip()]
    emit(get_client().patch(f"{API}/campaigns/{campaign_id}", json_body=body), fmt)


@campaigns.command("schedule")
@click.argument("campaign_id", type=int)
@click.option(
    "--at",
    "at_raw",
    required=True,
    help=(
        "ISO 8601 datetime to send at, e.g. 2026-11-05T10:00:00. "
        "Naive times use --tz; DST gaps are rejected and ambiguous times "
        "need an explicit UTC offset. The time must be in the future."
    ),
)
@click.option(
    "--tz",
    "tz_name",
    default=None,
    help=(
        "IANA timezone for a naive --at and for display, "
        "e.g. Europe/Berlin. Required when --at has no UTC offset."
    ),
)
@format_option
def campaigns_schedule(campaign_id, at_raw, tz_name, fmt):
    """Schedule a draft campaign for sending at a given time."""
    try:
        parsed = datetime.fromisoformat(at_raw.strip())
    except ValueError:
        raise click.UsageError(
            f"--at is not a valid ISO 8601 datetime: {at_raw!r}",
        )
    if parsed.tzinfo is None and not tz_name:
        raise click.UsageError(
            "--tz is required when --at has no UTC offset "
            '(e.g. --tz "Europe/Berlin").',
        )
    body = {"scheduled_at": at_raw}
    if tz_name:
        body["scheduled_timezone"] = tz_name
    emit(
        get_client().post(f"{API}/campaigns/{campaign_id}/schedule", json_body=body),
        fmt,
    )


@campaigns.command("unschedule")
@click.argument("campaign_id", type=int)
@format_option
def campaigns_unschedule(campaign_id, fmt):
    """Cancel a scheduled send (draft campaigns only)."""
    emit(get_client().post(f"{API}/campaigns/{campaign_id}/unschedule"), fmt)


@campaigns.command("send")
@click.argument("campaign_id", type=int)
@click.option(
    "--yes",
    "-y",
    is_flag=True,
    help="Confirm the send without an interactive prompt.",
)
@format_option
def campaigns_send(campaign_id, yes, fmt):
    """Send a draft campaign now (requires --yes in scripts)."""
    if not yes:
        click.confirm(
            f"Send campaign {campaign_id} to all eligible recipients now?",
            abort=True,
        )
    emit(get_client().post(f"{API}/campaigns/{campaign_id}/send"), fmt)


@campaigns.command("test-send")
@click.argument("campaign_id", type=int)
@click.option("--recipient", required=True, help="Exactly one test address.")
@format_option
def campaigns_test_send(campaign_id, recipient, fmt):
    """Send a TEST of a campaign to ONE address (never the campaign itself)."""
    emit(
        get_client().post(
            f"/studio/campaigns/{campaign_id}/test-send",
            json_body={"test_recipients": recipient},
        ),
        fmt,
    )


groups = [campaigns]
