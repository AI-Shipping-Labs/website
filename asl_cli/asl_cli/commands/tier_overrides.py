"""``asl tier-overrides`` -- grant and revoke tier overrides."""

from __future__ import annotations

import click

from asl_cli.commands._shared import emit, format_option, get_client

API = "/api"


@click.group(name="tier-overrides")
def tier_overrides():
    """Manage tier overrides."""


@tier_overrides.command("grant")
@click.option("--emails", required=True, help="Comma-separated emails.")
@click.option("--tier", required=True, help="Tier slug, e.g. main, premium.")
@format_option
def tier_overrides_grant(emails, tier, fmt):
    """Grant a tier override to one or more users."""
    body = {
        "emails": [e.strip() for e in emails.split(",") if e.strip()],
        "tier": tier,
    }
    emit(get_client().post(f"{API}/tier-overrides", json_body=body), fmt)


@tier_overrides.command("revoke")
@click.argument("emails", nargs=-1, required=True)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Report which overrides would be revoked without changing anything.",
)
@format_option
def tier_overrides_revoke(emails, dry_run, fmt):
    """Revoke every active tier override for one or more users.

    Each email reports revoked, no_active_override, or user_not_found.
    """
    body = {"emails": list(emails), "dry_run": dry_run}
    emit(get_client().post(f"{API}/tier-overrides/revoke", json_body=body), fmt)


groups = [tier_overrides]
