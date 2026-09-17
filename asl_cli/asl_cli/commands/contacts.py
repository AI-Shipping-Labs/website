"""``asl contacts`` -- bulk import/export, set-tags."""

from __future__ import annotations

import sys

import click

from asl_cli.commands._shared import (
    emit,
    format_option,
    format_option_with,
    get_client,
    json_option,
)

API = "/api"

# Columns shown by ``asl contacts export -f table``. The full column set only
# ships in the JSON and CSV outputs; the table is a review aid.
EXPORT_TABLE_COLUMNS = [
    "email", "first_name", "last_name", "tier", "tags", "unsubscribed",
]


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


def _write_verbatim(body: str) -> None:
    """Write a server body to stdout unchanged, with one trailing newline."""
    sys.stdout.write(body)
    if not body.endswith("\n"):
        sys.stdout.write("\n")


@contacts.command("export")
@format_option_with("csv")
def contacts_export(fmt):
    """Export all contacts.

    With -f csv the server's text/csv body is written to stdout verbatim, so
    'asl contacts export -f csv > contacts.csv' is directly parseable.
    """
    client = get_client()
    if fmt == "csv":
        _write_verbatim(
            client.get(f"{API}/contacts/export", params={"format": "csv"}, raw=True)
        )
        return

    data = client.get(f"{API}/contacts/export")
    if fmt == "table":
        rows = data.get("contacts", []) if isinstance(data, dict) else data
        emit(rows, fmt, columns=EXPORT_TABLE_COLUMNS)
    else:
        emit(data, fmt)


@contacts.command("set-tags")
@click.argument("email")
@click.option("--tags", required=True, help="Comma-separated tags, e.g. sprint:may-2026,workshop")
@format_option
def contacts_set_tags(email, tags, fmt):
    """Replace a contact's tag set (not additive)."""
    body = {"tags": [t.strip() for t in tags.split(",") if t.strip()]}
    emit(get_client().post(f"{API}/contacts/{email}/tags", json_body=body), fmt)


groups = [contacts]
