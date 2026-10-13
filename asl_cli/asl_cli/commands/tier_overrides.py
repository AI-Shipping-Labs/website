"""``asl tier-overrides`` -- list, grant and revoke tier overrides."""

from __future__ import annotations

import click

from asl_cli.commands._shared import emit, format_option, get_client

API = "/api"

LIST_TABLE_COLUMNS = [
    "email", "tier", "stored_tier", "raises_access", "expires_at", "source",
]


@click.group(name="tier-overrides")
def tier_overrides():
    """Manage tier overrides."""


@tier_overrides.command("list")
@click.option(
    "--status",
    type=click.Choice(["all", "effective", "ineffective"]),
    default="all",
    show_default=True,
    help=(
        "effective: grants that raise access; ineffective: grants at or "
        "below the member's stored tier (leftovers)."
    ),
)
@click.option("--limit", type=int, default=None, help="Maximum rows (API default 200).")
@format_option
def tier_overrides_list(status, limit, fmt):
    """List active tier overrides and whether each raises access."""
    params = {"status": status}
    if limit is not None:
        params["limit"] = limit
    data = get_client().get(f"{API}/tier-overrides", params=params)
    if fmt == "table":
        rows = data.get("overrides", []) if isinstance(data, dict) else data
        emit(rows, fmt, columns=LIST_TABLE_COLUMNS)
    else:
        emit(data, fmt)


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
@click.option(
    "--ineffective-only",
    is_flag=True,
    help=(
        "Only revoke grants that do not raise access (at or below the "
        "member's stored tier); grants that raise access stay active."
    ),
)
@format_option
def tier_overrides_revoke(emails, dry_run, ineffective_only, fmt):
    """Revoke every active tier override for one or more users.

    Each email reports revoked, no_active_override, or user_not_found.
    """
    body = {"emails": list(emails), "dry_run": dry_run}
    if ineffective_only:
        body["ineffective_only"] = True
    emit(get_client().post(f"{API}/tier-overrides/revoke", json_body=body), fmt)


groups = [tier_overrides]
