"""``asl`` commands for smaller surfaces: hosts, articles, tier-reconcile,
SES events, CRM export, cleanup-gates diagnostics."""

from __future__ import annotations

import click

from asl_cli.commands._shared import emit, format_option, get_client, json_arg

API = "/api"

commands = []


# -- hosts -------------------------------------------------------------------

@click.command("hosts-list")
@format_option
def hosts_list(fmt):
    """List event hosts."""
    data = get_client().get(f"{API}/hosts")
    emit(data, fmt)


commands.append(hosts_list)


@click.command("hosts-get")
@click.argument("slug")
@format_option
def hosts_get(slug, fmt):
    """Get a single host profile."""
    data = get_client().get(f"{API}/hosts/{slug}")
    emit(data, fmt)


commands.append(hosts_get)


# -- articles ----------------------------------------------------------------

@click.command("article-preview-link")
@click.argument("content_id")
@format_option
def article_preview_link(content_id, fmt):
    """Get an article's preview link."""
    data = get_client().get(f"{API}/articles/{content_id}/preview-link")
    emit(data, fmt)


commands.append(article_preview_link)


@click.command("article-preview-token-regenerate")
@click.argument("content_id")
@format_option
def article_preview_token_regenerate(content_id, fmt):
    """Regenerate an article's preview token."""
    data = get_client().post(f"{API}/articles/{content_id}/preview-token/regenerate")
    emit(data, fmt)


commands.append(article_preview_token_regenerate)


# -- tier reconcile ----------------------------------------------------------

@click.command("tier-reconcile-diagnostics")
@format_option
def tier_reconcile_diagnostics(fmt):
    """Tier reconciliation diagnostics."""
    data = get_client().get(f"{API}/payments/tier-reconcile/diagnostics")
    emit(data, fmt)


commands.append(tier_reconcile_diagnostics)


@click.command("tier-reconcile-apply")
@json_arg("data", required=True)
@format_option
def tier_reconcile_apply(data, fmt):
    """Apply tier reconciliation (JSON body)."""
    result = get_client().post(f"{API}/payments/tier-reconcile", json_body=data)
    emit(result, fmt)


commands.append(tier_reconcile_apply)


# -- ses events / crm export / diagnostics ----------------------------------

@click.command("ses-events")
@format_option
def ses_events(fmt):
    """Aggregate SES events list."""
    data = get_client().get(f"{API}/ses-events")
    emit(data, fmt)


commands.append(ses_events)


@click.command("crm-export")
@format_option
def crm_export(fmt):
    """Full CRM export (one row per user)."""
    data = get_client().get(f"{API}/crm/export")
    emit(data, fmt)


commands.append(crm_export)


@click.command("cleanup-gates-diagnostics")
@format_option
def cleanup_gates_diagnostics(fmt):
    """Cleanup-gate diagnostics (blocked row counts)."""
    data = get_client().get(f"{API}/diagnostics/cleanup-gates")
    emit(data, fmt)


commands.append(cleanup_gates_diagnostics)


# -- OpenAPI spec / docs -----------------------------------------------------

@click.command("openapi")
@format_option
def openapi(fmt):
    """Fetch the OpenAPI spec."""
    data = get_client().get(f"{API}/openapi.json")
    emit(data, fmt)


commands.append(openapi)
