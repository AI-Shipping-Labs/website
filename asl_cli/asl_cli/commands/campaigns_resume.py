"""``asl campaigns resume`` -- safely resume pending deliveries of a partial campaign."""

from __future__ import annotations

import click

from asl_cli.commands._shared import emit, format_option, get_client

API = "/api"


@click.command("resume")
@click.argument("campaign_id", type=int)
@click.option("--yes", is_flag=True, help="Confirm resuming pending recipients without prompting.")
@format_option
def campaigns_resume(campaign_id, yes, fmt):
    """Resume a partial campaign, queueing only pending recipients."""
    client = get_client()
    if not yes:
        campaign = client.get(f"{API}/campaigns/{campaign_id}")
        pending = (campaign.get("delivery_counts") or {}).get("pending", 0)
        sent = (campaign.get("delivery_counts") or {}).get("sent", 0)
        click.confirm(
            f"Resume campaign {campaign_id} ({pending} pending, {sent} already sent)?",
            abort=True,
        )
    emit(client.post(f"{API}/campaigns/{campaign_id}/resume", json_body={}), fmt)
