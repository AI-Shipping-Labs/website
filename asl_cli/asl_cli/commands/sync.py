"""``asl sync`` -- content sync sources and triggers."""

from __future__ import annotations

import click

from asl_cli.commands._shared import emit, format_option, get_client

API = "/api"

commands = []


@click.command("sync-sources-list")
@format_option
def sync_sources_list(fmt):
    """List registered content sync sources."""
    data = get_client().get(f"{API}/sync/sources")
    emit(data, fmt)


commands.append(sync_sources_list)


@click.command("sync-source-trigger")
@click.argument("source_id")
@format_option
def sync_source_trigger(source_id, fmt):
    """Trigger a content sync for one source."""
    data = get_client().post(f"{API}/sync/sources/{source_id}/trigger")
    emit(data, fmt)


commands.append(sync_source_trigger)
