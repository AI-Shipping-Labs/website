"""``asl contacts`` -- bulk import/export, set-tags, tier overrides."""

from __future__ import annotations

import click

from asl_cli.commands._shared import comma_list, emit, format_option, get_client, json_option

API = "/api"


@click.group()
def contacts():
    """Manage contacts and tier overrides."""


@contacts.command("import")
@json_option("data", required=True,
             help_text='JSON with "contacts" array, "default_tag", "default_tier".')
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
@comma_list("tags", "Comma-separated tags, e.g. sprint:may-2026,workshop")
@format_option
def contacts_set_tags(email, tags, fmt):
    """Replace a contact's tag set (not additive)."""
    body = {"tags": tags}
    emit(get_client().post(f"{API}/contacts/{email}/tags", json_body=body), fmt)


@contacts.command("grant-tier")
@click.option("--emails", required=True, help="Comma-separated emails.")
@click.option("--tier", required=True, help="Tier slug, e.g. main, premium.")
@format_option
def contacts_grant_tier(emails, tier, fmt):
    """Grant a tier override."""
    body = {
        "emails": [e.strip() for e in emails.split(",") if e.strip()],
        "tier": tier,
    }
    emit(get_client().post(f"{API}/tier-overrides", json_body=body), fmt)


groups = [contacts]
