"""``asl redirects`` -- URL redirect CRUD + bulk upsert."""

from __future__ import annotations

import click

from asl_cli.commands._shared import collect_flags, emit, format_option, get_client, json_option

API = "/api"


@click.group()
def redirects():
    """Manage URL redirects."""


@redirects.command("list")
@format_option
def redirects_list(fmt):
    """List URL redirects."""
    emit(get_client().get(f"{API}/redirects"), fmt)


@redirects.command("get")
@click.argument("redirect_id", type=int)
@format_option
def redirects_get(redirect_id, fmt):
    """Get a single redirect."""
    emit(get_client().get(f"{API}/redirects/{redirect_id}"), fmt)


REDIRECT_FLAGS = [
    click.option("--source-path", default=None),
    click.option("--target-path", default=None),
    click.option("--redirect-type", type=click.Choice(["301", "302"]), default=None),
    click.option("--is-active/--no-is-active", default=None),
]


def apply_redirect_flags(func):
    for decorator in reversed(REDIRECT_FLAGS):
        func = decorator(func)
    return func


@redirects.command("create")
@apply_redirect_flags
@format_option
def redirects_create(fmt, **kwargs):
    """Create a redirect. Use --help to see all flags."""
    body = collect_flags(click.get_current_context())
    if "redirect_type" in body:
        body["redirect_type"] = int(body["redirect_type"])
    emit(get_client().post(f"{API}/redirects", json_body=body), fmt)


@redirects.command("update")
@click.argument("redirect_id", type=int)
@apply_redirect_flags
@format_option
def redirects_update(redirect_id, fmt, **kwargs):
    """Update a redirect. Use --help to see all flags."""
    body = collect_flags(click.get_current_context())
    if "redirect_type" in body:
        body["redirect_type"] = int(body["redirect_type"])
    emit(get_client().patch(f"{API}/redirects/{redirect_id}", json_body=body), fmt)


@redirects.command("bulk-upsert")
@json_option("data", required=True, help_text='JSON with "redirects" array.')
@format_option
def redirects_bulk_upsert(data, fmt):
    """Bulk upsert redirects."""
    emit(get_client().post(f"{API}/redirects/bulk", json_body=data), fmt)


groups = [redirects]
