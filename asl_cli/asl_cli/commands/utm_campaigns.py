"""``asl utm-campaigns`` -- UTM campaign + tracked-link CRUD."""

from __future__ import annotations

import click

from asl_cli.commands._shared import emit, format_option, get_client, json_arg

API = "/api"

commands = []


@click.command("utm-campaigns-list")
@format_option
def utm_campaigns_list(fmt):
    """List UTM campaigns."""
    data = get_client().get(f"{API}/utm-campaigns")
    emit(data, fmt)


commands.append(utm_campaigns_list)


@click.command("utm-campaigns-get")
@click.argument("campaign_id", type=int)
@format_option
def utm_campaigns_get(campaign_id, fmt):
    """Get a single UTM campaign."""
    data = get_client().get(f"{API}/utm-campaigns/{campaign_id}")
    emit(data, fmt)


commands.append(utm_campaigns_get)


@click.command("utm-campaigns-create")
@json_arg("data", required=True)
@format_option
def utm_campaigns_create(data, fmt):
    """Create a UTM campaign (JSON body)."""
    result = get_client().post(f"{API}/utm-campaigns", json_body=data)
    emit(result, fmt)


commands.append(utm_campaigns_create)


@click.command("utm-campaigns-update")
@click.argument("campaign_id", type=int)
@json_arg("data", required=True)
@format_option
def utm_campaigns_update(campaign_id, data, fmt):
    """Update a UTM campaign (JSON body)."""
    result = get_client().patch(f"{API}/utm-campaigns/{campaign_id}", json_body=data)
    emit(result, fmt)


commands.append(utm_campaigns_update)


@click.command("utm-campaign-links")
@click.argument("campaign_id", type=int)
@format_option
def utm_campaign_links(campaign_id, fmt):
    """List tracked links for a UTM campaign."""
    data = get_client().get(f"{API}/utm-campaigns/{campaign_id}/links")
    emit(data, fmt)


commands.append(utm_campaign_links)


@click.command("utm-campaign-link-get")
@click.argument("campaign_id", type=int)
@click.argument("link_id", type=int)
@format_option
def utm_campaign_link_get(campaign_id, link_id, fmt):
    """Get a single tracked link."""
    data = get_client().get(f"{API}/utm-campaigns/{campaign_id}/links/{link_id}")
    emit(data, fmt)


commands.append(utm_campaign_link_get)
