"""``asl redirects`` -- URL redirect CRUD + bulk upsert."""

from __future__ import annotations

import click

from asl_cli.commands._shared import emit, format_option, get_client, json_arg

API = "/api"

commands = []


@click.command("redirects-list")
@format_option
def redirects_list(fmt):
    """List URL redirects."""
    data = get_client().get(f"{API}/redirects")
    emit(data, fmt)


commands.append(redirects_list)


@click.command("redirects-get")
@click.argument("redirect_id", type=int)
@format_option
def redirects_get(redirect_id, fmt):
    """Get a single redirect."""
    data = get_client().get(f"{API}/redirects/{redirect_id}")
    emit(data, fmt)


commands.append(redirects_get)


@click.command("redirects-create")
@json_arg("data", required=True)
@format_option
def redirects_create(data, fmt):
    """Create a redirect (JSON body)."""
    result = get_client().post(f"{API}/redirects", json_body=data)
    emit(result, fmt)


commands.append(redirects_create)


@click.command("redirects-update")
@click.argument("redirect_id", type=int)
@json_arg("data", required=True)
@format_option
def redirects_update(redirect_id, data, fmt):
    """Update a redirect (JSON body)."""
    result = get_client().patch(f"{API}/redirects/{redirect_id}", json_body=data)
    emit(result, fmt)


commands.append(redirects_update)


@click.command("redirects-bulk-upsert")
@json_arg("data", required=True)
@format_option
def redirects_bulk_upsert(data, fmt):
    """Bulk upsert redirects (JSON body)."""
    result = get_client().post(f"{API}/redirects/bulk", json_body=data)
    emit(result, fmt)


commands.append(redirects_bulk_upsert)
