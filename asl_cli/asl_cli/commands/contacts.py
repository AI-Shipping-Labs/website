"""``asl contacts`` -- bulk import/export, set-tags, tier overrides."""

from __future__ import annotations

import click

from asl_cli.commands._shared import emit, format_option, get_client, json_arg

API = "/api"

commands = []


@click.command("contacts-import")
@json_arg("data", required=True)
@format_option
def contacts_import(data, fmt):
    """Bulk-import contacts (JSON body with 'contacts' list)."""
    result = get_client().post(f"{API}/contacts/import", json_body=data)
    emit(result, fmt)


commands.append(contacts_import)


@click.command("contacts-export")
@click.option("--format", "output_format", type=click.Choice(["json", "csv"]), default="json")
@format_option
def contacts_export(output_format, fmt):
    """Export all contacts."""
    params = {"format": output_format}
    data = get_client().get(f"{API}/contacts/export", params=params)
    emit(data, fmt)


commands.append(contacts_export)


@click.command("contacts-set-tags")
@click.argument("email")
@json_arg("data", required=True)
@format_option
def contacts_set_tags(email, data, fmt):
    """Replace a contact's tag set (JSON body with 'tags' list)."""
    result = get_client().post(f"{API}/contacts/{email}/tags", json_body=data)
    emit(result, fmt)


commands.append(contacts_set_tags)


@click.command("tier-overrides-grant")
@json_arg("data", required=True)
@format_option
def tier_overrides_grant(data, fmt):
    """Grant a tier override (JSON body)."""
    result = get_client().post(f"{API}/tier-overrides", json_body=data)
    emit(result, fmt)


commands.append(tier_overrides_grant)
