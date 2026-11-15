"""``asl campaigns stop`` -- stop an active Relay campaign and reconcile."""

from __future__ import annotations

import click

from asl_cli.commands._shared import emit, format_option, get_client

API = "/api"


@click.command("stop")
@click.argument("campaign_id", type=int)
@click.option("--yes", is_flag=True, help="Confirm stopping the Relay campaign without prompting.")
@format_option
def campaigns_stop(campaign_id, yes, fmt):
    """Stop an active Relay campaign and reconcile local deliveries."""
    client = get_client()
    if not yes:
        click.confirm(
            f"Stop active Relay waves for campaign {campaign_id}?",
            abort=True,
        )
    emit(client.post(f"{API}/campaigns/{campaign_id}/stop", json_body={}), fmt)


@click.command("cancel")
@click.argument("campaign_id", type=int)
@click.option("--yes", is_flag=True, help="Confirm cancelling the campaign without prompting.")
@format_option
def campaigns_cancel(campaign_id, yes, fmt):
    """Permanently cancel a campaign and halt any active Relay waves."""
    client = get_client()
    if not yes:
        click.confirm(
            f"Permanently cancel campaign {campaign_id} and halt active Relay waves?",
            abort=True,
        )
    emit(client.post(f"{API}/campaigns/{campaign_id}/cancel", json_body={}), fmt)
