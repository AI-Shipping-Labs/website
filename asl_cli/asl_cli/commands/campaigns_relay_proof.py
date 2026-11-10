"""Guarded internal-only Relay bulk proof commands."""

from __future__ import annotations

import click

from asl_cli.commands._shared import emit, format_option, get_client

API = "/api"


@click.command("relay-proof")
@click.argument("campaign_id", type=int)
@click.option("--dry-run", "dry_run", is_flag=True, help="Validate the list and print a signed key.")
@click.option("--confirm", "campaign_key", metavar="CAMPAIGN_KEY", help="Queue the proof with a dry-run key.")
@format_option
def campaigns_relay_proof(campaign_id, dry_run, campaign_key, fmt):
    """Dry-run or queue the bulk proof to the internal test list only."""
    if dry_run == (campaign_key is not None):
        raise click.UsageError("Pass exactly one of --dry-run or --confirm.")
    client = get_client()
    base = f"{API}/campaigns/{campaign_id}/relay-proof"
    if dry_run:
        emit(client.post(f"{base}/dry-run"), fmt)
        return
    emit(client.post(f"{base}/confirm", json_body={"campaign_key": campaign_key}), fmt)


@click.command("relay-proof-stats")
@click.argument("campaign_id", type=int)
@click.argument("campaign_key")
@format_option
def campaigns_relay_proof_stats(campaign_id, campaign_key, fmt):
    """Read Relay delivery stats for a bulk proof key."""
    emit(get_client().get(f"{API}/campaigns/{campaign_id}/relay-proof/{campaign_key}"), fmt)
