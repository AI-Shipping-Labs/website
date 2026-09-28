"""``asl content`` -- look up synced content by its frontmatter UUID."""

from __future__ import annotations

import sys
import uuid

import click

from asl_cli.commands._shared import emit, format_option, get_client

API = "/api"

TABLE_COLUMNS = ["type", "title", "url", "is_public", "commit", "updated_at"]


@click.group()
def content():
    """Inspect synced content (articles, courses, units, ...) by content_id."""


def _parse_content_id(value: str) -> str:
    try:
        return str(uuid.UUID(value.strip()))
    except (ValueError, AttributeError):
        raise click.ClickException(
            f"Invalid content_id: {value} (expected a UUID)"
        ) from None


def _table_row(data: dict) -> dict:
    commit = (data.get("source") or {}).get("commit") or ""
    return {
        "type": data.get("type"),
        "title": data.get("title"),
        "url": data.get("url"),
        "is_public": data.get("is_public"),
        "commit": commit[:7],
        "updated_at": data.get("updated_at"),
    }


@content.command("get")
@click.argument("content_id")
@click.option(
    "--no-body",
    is_flag=True,
    default=False,
    help="Skip markdown/html bodies (sends include_body=false); returns lengths.",
)
@click.option(
    "--body",
    "body_field",
    type=click.Choice(["markdown", "html"]),
    default=None,
    help="Print only the stored markdown or HTML string, verbatim (ignores --format).",
)
@format_option
def content_get(content_id, no_body, body_field, fmt):
    """Get one synced content row by CONTENT_ID (frontmatter UUID).

    Returns metadata, source repo/path/commit, stored markdown, and stored
    HTML. Use ``--body markdown`` or ``--body html`` to pipe the raw text
    into grep or diff.
    """
    if no_body and body_field:
        raise click.UsageError("--body and --no-body cannot be used together.")
    normalized = _parse_content_id(content_id)

    client = get_client()
    path = f"{API}/content/{normalized}"
    if no_body:
        data = client.get(path, params={"include_body": "false"})
    else:
        data = client.get(path)

    if body_field:
        value = data.get(body_field) if isinstance(data, dict) else None
        if value is None:
            content_type = data.get("type", "unknown") if isinstance(data, dict) else "unknown"
            label = "HTML" if body_field == "html" else "markdown"
            raise click.ClickException(
                f"No stored {label} for this content type ({content_type})"
            )
        sys.stdout.write(value + "\n")
        return

    if fmt == "table":
        emit(_table_row(data), fmt, columns=TABLE_COLUMNS)
        return
    emit(data, fmt)


groups = [content]
