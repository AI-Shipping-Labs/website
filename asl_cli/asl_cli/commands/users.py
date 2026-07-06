"""``asl users`` -- user lookup, tags, aliases, merge, deliverability."""

from __future__ import annotations

import click

from asl_cli.commands._shared import emit, format_option, get_client

API = "/api"


@click.group()
def users():
    """Manage users and CRM."""


# -- list / get / patch ------------------------------------------------------

@users.command("list")
@click.option("-q", "--query", default="", help="Substring search.")
@click.option("--limit", type=int, default=50)
@format_option
def users_list(query, limit, fmt):
    """List / search users."""
    params = {"limit": limit}
    if query:
        params["q"] = query
    data = get_client().get(f"{API}/users", params=params)
    if fmt == "table":
        emit(data.get("users", []) if isinstance(data, dict) else data, fmt,
             columns=["email", "display_name", "tier", "unsubscribed", "bounce_state"])
    else:
        emit(data, fmt)


@users.command("get")
@click.argument("email")
@format_option
def users_get(email, fmt):
    """Get a single user's full state."""
    emit(get_client().get(f"{API}/users/{email}"), fmt)


@users.command("patch")
@click.argument("email")
@click.option("--unsubscribed/--no-unsubscribed", default=None)
@click.option("--email-verified/--no-email-verified", default=None,
              help="Can only be set to true (verify).")
@format_option
def users_patch(email, unsubscribed, email_verified, fmt):
    """Patch a user (unsubscribed / email_verified)."""
    body = {}
    if unsubscribed is not None:
        body["unsubscribed"] = unsubscribed
    if email_verified is not None:
        body["email_verified"] = email_verified
    emit(get_client().patch(f"{API}/users/{email}", json_body=body), fmt)


# -- tags --------------------------------------------------------------------

@click.group(name="tags")
def users_tags():
    """Manage user tags."""


@users_tags.command("add")
@click.argument("email")
@click.argument("tag")
@format_option
def users_tags_add(email, tag, fmt):
    """Add a tag."""
    emit(get_client().post(f"{API}/users/{email}/tags", json_body={"tag": tag}), fmt)


@users_tags.command("remove")
@click.argument("email")
@click.argument("tag")
@format_option
def users_tags_remove(email, tag, fmt):
    """Remove a tag."""
    emit(get_client().delete(f"{API}/users/{email}/tags/{tag}"), fmt)


users.add_command(users_tags)


# -- aliases -----------------------------------------------------------------

@click.group(name="aliases")
def users_aliases():
    """Manage email aliases."""


@users_aliases.command("add")
@click.argument("email")
@click.option("--alias-email", required=True)
@click.option("--note", default=None)
@format_option
def users_aliases_add(email, alias_email, note, fmt):
    """Add an email alias."""
    body = {"alias_email": alias_email}
    if note:
        body["note"] = note
    emit(get_client().post(f"{API}/users/{email}/aliases", json_body=body), fmt)


@users_aliases.command("remove")
@click.argument("email")
@click.argument("alias_email")
@format_option
def users_aliases_remove(email, alias_email, fmt):
    """Remove an email alias."""
    emit(get_client().delete(f"{API}/users/{email}/aliases/{alias_email}"), fmt)


users.add_command(users_aliases)


# -- notes -------------------------------------------------------------------

@click.group(name="notes")
def users_notes():
    """Manage member notes."""


@users_notes.command("list")
@click.argument("email")
@format_option
def users_notes_list(email, fmt):
    """List member notes for a user."""
    emit(get_client().get(f"{API}/users/{email}/notes"), fmt)


@users_notes.command("add")
@click.argument("email")
@click.option("--body", required=True, help="Note body text.")
@click.option("--kind", type=click.Choice([
    "action_item", "background", "general", "intake",
    "meeting", "persona", "recommendation", "source",
]), default="general")
@click.option("--visibility", type=click.Choice(["internal", "external"]), default="internal")
@click.option("--plan-id", type=int, default=None)
@format_option
def users_notes_add(email, body, kind, visibility, plan_id, fmt):
    """Add a member note."""
    payload = {"user_email": email, "body": body, "kind": kind, "visibility": visibility}
    if plan_id is not None:
        payload["plan_id"] = plan_id
    emit(get_client().post(f"{API}/member-notes", json_body=payload), fmt)


users.add_command(users_notes)


# -- merge -------------------------------------------------------------------

@users.command("merge")
@click.option("--canonical-email", required=True, help="Account to keep.")
@click.option("--merge-email", required=True, help="Account to fold in.")
@click.option("--dry-run", is_flag=True, default=False)
@click.option("--force", is_flag=True, default=False)
@format_option
def users_merge(canonical_email, merge_email, dry_run, force, fmt):
    """Merge a duplicate account into the canonical one."""
    body = {
        "canonical_email": canonical_email,
        "merge_email": merge_email,
        "dry_run": dry_run,
        "force": force,
    }
    emit(get_client().post(f"{API}/users/merge", json_body=body), fmt)


# -- deliverability ----------------------------------------------------------

@users.command("mark-bounced")
@click.argument("email")
@click.option("--bounce-type", type=click.Choice(["permanent", "soft"]), required=True)
@click.option("--reason", default=None)
@click.option("--diagnostic", default=None)
@format_option
def users_mark_bounced(email, bounce_type, reason, diagnostic, fmt):
    """Mark a user as bounced."""
    body = {"bounce_type": bounce_type}
    if reason:
        body["reason"] = reason
    if diagnostic:
        body["diagnostic"] = diagnostic
    emit(get_client().post(f"{API}/users/{email}/mark-bounced", json_body=body), fmt)


@users.command("email-log")
@click.argument("email")
@click.option("--limit", type=int, default=50)
@click.option("--kind", default=None)
@format_option
def users_email_log(email, limit, kind, fmt):
    """Outbound email log for a user."""
    params = {"limit": limit}
    if kind:
        params["kind"] = kind
    emit(get_client().get(f"{API}/users/{email}/email-log", params=params), fmt)


@users.command("ses-events")
@click.argument("email")
@click.option("--limit", type=int, default=50)
@click.option("--type", "event_type", default=None)
@format_option
def users_ses_events(email, limit, event_type, fmt):
    """Inbound SES events for a user."""
    params = {"limit": limit}
    if event_type:
        params["type"] = event_type
    emit(get_client().get(f"{API}/users/{email}/ses-events", params=params), fmt)


# -- activity / crm ----------------------------------------------------------

@users.command("activity")
@click.argument("email")
@click.option("--limit", type=int, default=50)
@click.option("--category", default=None)
@format_option
def users_activity(email, limit, category, fmt):
    """Activity context for a user."""
    params = {"limit": limit}
    if category:
        params["category"] = category
    emit(get_client().get(f"{API}/users/{email}/activity", params=params), fmt)


@users.command("crm-record")
@click.argument("email")
@format_option
def users_crm_record(email, fmt):
    """CRM record for a user."""
    emit(get_client().get(f"{API}/users/{email}/crm-record"), fmt)


@users.command("payment-mismatches")
@format_option
def users_payment_mismatches(fmt):
    """List payment mismatches."""
    emit(get_client().get(f"{API}/users/payment-mismatches"), fmt)


groups = [users]
