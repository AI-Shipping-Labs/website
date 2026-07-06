"""``asl contacts`` -- bulk import/export, set-tags."""

from __future__ import annotations

import click

from asl_cli.commands._shared import emit, format_option, get_client, json_option

API = "/api"


@click.group()
def contacts():
    """Manage contacts."""


@contacts.command("import")
@json_option("data", required=True,
             help_text='JSON {"contacts":[...], "default_tag":"...", "default_tier":"..."}')
@format_option
def contacts_import(data, fmt):
    """Bulk-import contacts."""
    emit(get_client().post(f"{API}/contacts/import", json_body=data), fmt)


@contacts.command("export")
@click.option("--format", "output_format", type=click.Choice(["json", "csv"]), default="json")
@format_option
def contacts_export(output_format, fmt):
    """Export all contacts."""
    emit(get_client().get(f"{API}/contacts/export", params={"format": output_format}), fmt)


@contacts.command("set-tags")
@click.argument("email")
@click.option("--tags", required=True, help="Comma-separated tags, e.g. sprint:may-2026,workshop")
@format_option
def contacts_set_tags(email, tags, fmt):
    """Replace a contact's tag set (not additive)."""
    body = {"tags": [t.strip() for t in tags.split(",") if t.strip()]}
    emit(get_client().post(f"{API}/contacts/{email}/tags", json_body=body), fmt)


groups = [contacts]
