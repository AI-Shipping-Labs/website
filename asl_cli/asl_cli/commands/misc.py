"""Smaller surfaces: hosts, articles, diagnostics, and openapi."""

from __future__ import annotations

import click

from asl_cli.commands._shared import emit, format_option, get_client

API = "/api"
CRM_EXPORT_PAGE_SIZE = 200

groups = []


# -- hosts -------------------------------------------------------------------

@click.group()
def hosts():
    """Manage event host profiles."""


@hosts.command("list")
@format_option
def hosts_list(fmt):
    """List event hosts."""
    emit(get_client().get(f"{API}/hosts"), fmt)


@hosts.command("get")
@click.argument("slug")
@format_option
def hosts_get(slug, fmt):
    """Get a single host profile."""
    emit(get_client().get(f"{API}/hosts/{slug}"), fmt)


groups.append(hosts)


# -- articles ----------------------------------------------------------------

@click.group()
def articles():
    """Manage article preview links."""


@articles.command("preview-link")
@click.argument("content_id")
@format_option
def article_preview_link(content_id, fmt):
    """Get an article's preview link."""
    emit(get_client().get(f"{API}/articles/{content_id}/preview-link"), fmt)


@articles.command("regenerate-preview-token")
@click.argument("content_id")
@format_option
def article_preview_token_regenerate(content_id, fmt):
    """Regenerate an article's preview token."""
    emit(get_client().post(f"{API}/articles/{content_id}/preview-token/regenerate"), fmt)


groups.append(articles)


# -- standalone read commands ------------------------------------------------

@click.command("ses-events")
@format_option
def ses_events(fmt):
    """Aggregate SES events list."""
    emit(get_client().get(f"{API}/ses-events"), fmt)


groups.append(ses_events)


@click.command("crm-export")
@format_option
def crm_export(fmt):
    """Full CRM export (one row per user), fetching every API page."""
    emit(_crm_export_all(get_client()), fmt)


def _crm_export_all(client):
    """Collect all CRM export pages before returning a single response."""
    offset = 0
    expected_total = None
    result = None
    members = []
    while expected_total is None or offset < expected_total:
        page = client.get(
            f"{API}/crm/export",
            params={"limit": CRM_EXPORT_PAGE_SIZE, "offset": offset},
        )
        total, page_members = _validate_crm_export_page(page, offset, expected_total)
        if expected_total is None:
            expected_total = total
            result = dict(page)
        members.extend(page_members)
        offset += len(page_members)

    if result is None:
        raise click.ClickException("CRM export API returned no pages.")
    result["members"] = members
    result["count"] = len(members)
    result["offset"] = 0
    return result


def _validate_crm_export_page(page, offset, expected_total):
    """Validate paging metadata and return the page's total and rows."""
    if not isinstance(page, dict) or not isinstance(page.get("members"), list):
        raise click.ClickException("CRM export API returned an invalid members page.")
    total = page.get("total")
    if isinstance(total, bool) or not isinstance(total, int) or total < 0:
        raise click.ClickException("CRM export API returned an invalid total count.")
    if expected_total is not None and total != expected_total:
        raise click.ClickException(
            "CRM export total changed during pagination; rerun the export."
        )
    if page.get("offset") != offset:
        raise click.ClickException(
            f"CRM export API returned offset {page.get('offset')!r}; expected {offset}."
        )
    rows = page["members"]
    if not rows and offset < total:
        raise click.ClickException(f"CRM export stopped at {offset} of {total} rows.")
    return total, rows


groups.append(crm_export)


@click.command("cleanup-gates")
@format_option
def cleanup_gates(fmt):
    """Cleanup-gate diagnostics (blocked row counts)."""
    emit(get_client().get(f"{API}/diagnostics/cleanup-gates"), fmt)


groups.append(cleanup_gates)


@click.command("openapi")
@format_option
def openapi(fmt):
    """Fetch the OpenAPI spec."""
    emit(get_client().get(f"{API}/openapi.json"), fmt)


groups.append(openapi)
