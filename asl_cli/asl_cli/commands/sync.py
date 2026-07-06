"""``asl sync`` -- content sync sources and triggers."""

from __future__ import annotations

import click

from asl_cli.commands._shared import emit, format_option, get_client

API = "/api"


@click.group()
def sync():
    """Manage content sync."""


@sync.command("sources")
@format_option
def sync_sources(fmt):
    """List registered content sync sources."""
    emit(get_client().get(f"{API}/sync/sources"), fmt)


@sync.command("trigger")
@click.argument("source_id")
@format_option
def sync_trigger(source_id, fmt):
    """Trigger a content sync for one source."""
    emit(get_client().post(f"{API}/sync/sources/{source_id}/trigger"), fmt)


groups = [sync]
