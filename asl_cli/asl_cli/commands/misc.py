"""Smaller surfaces: hosts, articles, tier-reconcile, diagnostics, openapi."""

from __future__ import annotations

import click

from asl_cli.commands._shared import emit, format_option, get_client, json_option

API = "/api"

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


# -- tier-reconcile ----------------------------------------------------------

@click.group(name="tier-reconcile")
def tier_reconcile():
    """Tier reconciliation."""


@tier_reconcile.command("diagnostics")
@format_option
def tier_reconcile_diagnostics(fmt):
    """Tier reconciliation diagnostics."""
    emit(get_client().get(f"{API}/payments/tier-reconcile/diagnostics"), fmt)


@tier_reconcile.command("apply")
@json_option("data", required=True)
@format_option
def tier_reconcile_apply(data, fmt):
    """Apply tier reconciliation."""
    emit(get_client().post(f"{API}/payments/tier-reconcile", json_body=data), fmt)


groups.append(tier_reconcile)


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
    """Full CRM export (one row per user)."""
    emit(get_client().get(f"{API}/crm/export"), fmt)


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
