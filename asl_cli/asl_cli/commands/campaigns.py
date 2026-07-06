"""``asl campaigns`` -- email campaign CRUD."""

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
def campaigns():
    """Manage email campaigns."""


@campaigns.command("list")
@click.option("--status", default=None, help="Filter by status.")
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
    click.option("--target-min-level", type=TierLevel(), default=None,
                 help="open, basic, main, premium (or integer)."),
    click.option("--target-tags-any", default=None, help="Comma-separated tags."),
    click.option("--target-tags-none", default=None, help="Comma-separated tags."),
    click.option("--slack-filter", default=None),
    click.option("--audience-verification", default=None),
    click.option("--target-event", type=int, default=None, help="Event id or omit."),
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
    """Create a campaign draft. Use --help to see all flags."""
    body = collect_flags(click.get_current_context())
    # Split comma-separated tag lists.
    for key in ("target_tags_any", "target_tags_none"):
        if key in body and isinstance(body[key], str):
            body[key] = [t.strip() for t in body[key].split(",") if t.strip()]
    emit(get_client().post(f"{API}/campaigns", json_body=body), fmt)


@campaigns.command("update")
@click.argument("campaign_id", type=int)
@apply_campaign_flags
@format_option
def campaigns_update(campaign_id, fmt, **kwargs):
    """Update a campaign. Use --help to see all flags."""
    body = collect_flags(click.get_current_context())
    for key in ("target_tags_any", "target_tags_none"):
        if key in body and isinstance(body[key], str):
            body[key] = [t.strip() for t in body[key].split(",") if t.strip()]
    emit(get_client().patch(f"{API}/campaigns/{campaign_id}", json_body=body), fmt)


groups = [campaigns]
