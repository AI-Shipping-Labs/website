"""``asl utm-campaigns`` -- UTM campaign + tracked-link CRUD."""

from __future__ import annotations

import click

from asl_cli.commands._shared import collect_flags, emit, format_option, get_client

API = "/api"


@click.group(name="utm-campaigns")
def utm_campaigns():
    """Manage UTM campaigns and tracked links."""


@utm_campaigns.command("list")
@click.option("--is-archived", type=click.Choice(["true", "false"]), default=None)
@click.option("-q", "--query", default=None)
@format_option
def utm_campaigns_list(is_archived, query, fmt):
    """List UTM campaigns."""
    params = {}
    if is_archived:
        params["is_archived"] = is_archived
    if query:
        params["q"] = query
    emit(get_client().get(f"{API}/utm-campaigns", params=params or None), fmt)


@utm_campaigns.command("get")
@click.argument("campaign_id", type=int)
@format_option
def utm_campaigns_get(campaign_id, fmt):
    """Get a single UTM campaign with links."""
    emit(get_client().get(f"{API}/utm-campaigns/{campaign_id}"), fmt)


UTM_FLAGS = [
    click.option("--name", default=None),
    click.option("--slug", default=None),
    click.option("--default-source", default=None, help="Default utm_source."),
    click.option("--default-medium", default=None, help="Default utm_medium."),
    click.option("--notes", default=None),
    click.option("--is-archived/--no-is-archived", default=None),
]


def apply_utm_flags(func):
    for decorator in reversed(UTM_FLAGS):
        func = decorator(func)
    return func


@utm_campaigns.command("create")
@apply_utm_flags
@format_option
def utm_campaigns_create(fmt, **kwargs):
    """Create a UTM campaign. Use --help to see all flags."""
    body = collect_flags(click.get_current_context())
    emit(get_client().post(f"{API}/utm-campaigns", json_body=body), fmt)


@utm_campaigns.command("update")
@click.argument("campaign_id", type=int)
@apply_utm_flags
@format_option
def utm_campaigns_update(campaign_id, fmt, **kwargs):
    """Update a UTM campaign. Use --help to see all flags."""
    body = collect_flags(click.get_current_context())
    emit(get_client().patch(f"{API}/utm-campaigns/{campaign_id}", json_body=body), fmt)


# -- links (nested) ----------------------------------------------------------

@click.group(name="links")
def utm_links():
    """Manage tracked links."""


@utm_links.command("list")
@click.argument("campaign_id", type=int)
@format_option
def utm_campaign_links(campaign_id, fmt):
    """List tracked links for a UTM campaign."""
    emit(get_client().get(f"{API}/utm-campaigns/{campaign_id}/links"), fmt)


@utm_links.command("get")
@click.argument("campaign_id", type=int)
@click.argument("link_id", type=int)
@format_option
def utm_campaign_link_get(campaign_id, link_id, fmt):
    """Get a single tracked link."""
    emit(get_client().get(f"{API}/utm-campaigns/{campaign_id}/links/{link_id}"), fmt)


utm_campaigns.add_command(utm_links)


groups = [utm_campaigns]
