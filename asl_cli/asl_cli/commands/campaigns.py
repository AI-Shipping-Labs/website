"""``asl campaigns`` -- email campaign CRUD."""

from __future__ import annotations

import click

from asl_cli.commands._shared import emit, format_option, get_client, json_arg

API = "/api"

commands = []


@click.command("campaigns-list")
@format_option
def campaigns_list(fmt):
    """List email campaigns."""
    data = get_client().get(f"{API}/campaigns")
    emit(data, fmt)


commands.append(campaigns_list)


@click.command("campaigns-get")
@click.argument("campaign_id", type=int)
@format_option
def campaigns_get(campaign_id, fmt):
    """Get a single campaign."""
    data = get_client().get(f"{API}/campaigns/{campaign_id}")
    emit(data, fmt)


commands.append(campaigns_get)


@click.command("campaigns-create")
@json_arg("data", required=True)
@format_option
def campaigns_create(data, fmt):
    """Create a campaign (JSON body)."""
    result = get_client().post(f"{API}/campaigns", json_body=data)
    emit(result, fmt)


commands.append(campaigns_create)


@click.command("campaigns-update")
@click.argument("campaign_id", type=int)
@json_arg("data", required=True)
@format_option
def campaigns_update(campaign_id, data, fmt):
    """Update a campaign (JSON body)."""
    result = get_client().patch(f"{API}/campaigns/{campaign_id}", json_body=data)
    emit(result, fmt)


commands.append(campaigns_update)
