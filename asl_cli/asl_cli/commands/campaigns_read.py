"""Read-only campaign audience and wave listings."""

from __future__ import annotations

import click

from asl_cli.commands._shared import emit, format_option, get_client

API = "/api"

RECIPIENT_TABLE_COLUMNS = [
    "email", "delivery_state", "disposition", "sent_at", "attempt_count",
    "opens", "clicks", "last_error",
]
WAVE_TABLE_COLUMNS = [
    "number", "state", "recipient_count", "confirmed_sent", "skipped",
    "failed_ambiguous", "bounces", "bounce_rate", "complaints",
    "complaint_rate", "released_at", "completed_at",
]


def _list_output(fmt, as_json, payload, collection, columns, empty_message):
    """Render a collection as JSON or as a focused table."""
    if as_json and fmt != "json":
        raise click.UsageError("--json cannot be combined with --format table or raw.")
    if fmt != "table":
        output_format = fmt
        if as_json:
            output_format = "json"
        emit(payload, output_format)
        return
    rows = payload.get(collection, [])
    if not rows:
        click.echo(empty_message)
        return
    emit(rows, fmt, columns=columns)


@click.command("recipients")
@click.argument("campaign_id", type=int)
@click.option("--json", "as_json", is_flag=True, help="Output the full JSON response.")
@format_option
def campaigns_recipients(campaign_id, as_json, fmt):
    """List the recipient preview or delivery log for a campaign."""
    payload = get_client().get(f"{API}/campaigns/{campaign_id}/recipients")
    _list_output(
        fmt, as_json, payload, "recipients", RECIPIENT_TABLE_COLUMNS,
        "No campaign recipients.",
    )


@click.command("waves")
@click.argument("campaign_id", type=int)
@click.option("--json", "as_json", is_flag=True, help="Output the full JSON response.")
@format_option
def campaigns_waves(campaign_id, as_json, fmt):
    """List monitored delivery waves for a campaign."""
    payload = get_client().get(f"{API}/campaigns/{campaign_id}/waves")
    _list_output(
        fmt, as_json, payload, "waves", WAVE_TABLE_COLUMNS,
        "No delivery waves.",
    )
