"""``asl users`` -- user lookup, tags, aliases, merge, deliverability."""

from __future__ import annotations

import click

from asl_cli.commands._shared import emit, format_option, get_client, json_arg

API = "/api"

commands = []


# -- search / list -----------------------------------------------------------

@click.command("users-list")
@click.option("-q", "--query", default="", help="Substring search.")
@click.option("--limit", type=int, default=50, help="Max results (default 50).")
@format_option
def users_list(query, limit, fmt):
    """List / search users."""
    params = {"limit": limit}
    if query:
        params["q"] = query
    data = get_client().get(f"{API}/users", params=params)
    if fmt == "table":
        rows = data.get("users", []) if isinstance(data, dict) else data
        emit(rows, fmt, columns=["email", "display_name", "tier", "unsubscribed", "bounce_state"])
    else:
        emit(data, fmt)


commands.append(users_list)


@click.command("users-get")
@click.argument("email")
@format_option
def users_get(email, fmt):
    """Get a single user's full state."""
    data = get_client().get(f"{API}/users/{email}")
    emit(data, fmt)


commands.append(users_get)


@click.command("users-patch")
@click.argument("email")
@json_arg("data", required=True)
@format_option
def users_patch(email, data, fmt):
    """Patch a user (unsubscribed / email_verified)."""
    result = get_client().patch(f"{API}/users/{email}", json_body=data)
    emit(result, fmt)


commands.append(users_patch)


# -- tags --------------------------------------------------------------------

@click.command("users-tags-add")
@click.argument("email")
@click.argument("tag")
@format_option
def users_tags_add(email, tag, fmt):
    """Add a tag to a user."""
    data = get_client().post(f"{API}/users/{email}/tags", json_body={"tag": tag})
    emit(data, fmt)


commands.append(users_tags_add)


@click.command("users-tags-remove")
@click.argument("email")
@click.argument("tag")
@format_option
def users_tags_remove(email, tag, fmt):
    """Remove a tag from a user."""
    data = get_client().delete(f"{API}/users/{email}/tags/{tag}")
    emit(data, fmt)


commands.append(users_tags_remove)


# -- aliases -----------------------------------------------------------------

@click.command("users-aliases-add")
@click.argument("email")
@click.option("--alias-email", required=True, help="Alias email to add.")
@click.option("--note", default=None, help="Optional note.")
@format_option
def users_aliases_add(email, alias_email, note, fmt):
    """Add an email alias to a user."""
    body = {"alias_email": alias_email}
    if note:
        body["note"] = note
    data = get_client().post(f"{API}/users/{email}/aliases", json_body=body)
    emit(data, fmt)


commands.append(users_aliases_add)


@click.command("users-aliases-remove")
@click.argument("email")
@click.argument("alias_email")
@format_option
def users_aliases_remove(email, alias_email, fmt):
    """Remove an email alias from a user."""
    data = get_client().delete(f"{API}/users/{email}/aliases/{alias_email}")
    emit(data, fmt)


commands.append(users_aliases_remove)


# -- merge -------------------------------------------------------------------

@click.command("users-merge")
@click.option("--canonical-email", required=True, help="Account to keep.")
@click.option("--merge-email", required=True, help="Account to fold in.")
@click.option("--dry-run", is_flag=True, default=False, help="Preview without writing.")
@click.option("--force", is_flag=True, default=False, help="Override safety checks.")
@format_option
def users_merge(canonical_email, merge_email, dry_run, force, fmt):
    """Merge a duplicate account into the canonical one."""
    body = {
        "canonical_email": canonical_email,
        "merge_email": merge_email,
        "dry_run": dry_run,
        "force": force,
    }
    data = get_client().post(f"{API}/users/merge", json_body=body)
    emit(data, fmt)


commands.append(users_merge)


# -- deliverability ----------------------------------------------------------

@click.command("users-mark-bounced")
@click.argument("email")
@click.option("--bounce-type", type=click.Choice(["permanent", "soft"]), required=True)
@click.option("--reason", default="", help="Bounce reason.")
@click.option("--diagnostic", default="", help="SMTP diagnostic.")
@format_option
def users_mark_bounced(email, bounce_type, reason, diagnostic, fmt):
    """Mark a user as bounced."""
    body = {"bounce_type": bounce_type}
    if reason:
        body["reason"] = reason
    if diagnostic:
        body["diagnostic"] = diagnostic
    data = get_client().post(f"{API}/users/{email}/mark-bounced", json_body=body)
    emit(data, fmt)


commands.append(users_mark_bounced)


@click.command("users-email-log")
@click.argument("email")
@click.option("--limit", type=int, default=50)
@click.option("--kind", default=None, help="Filter by email kind.")
@format_option
def users_email_log(email, limit, kind, fmt):
    """Outbound email log for a user."""
    params = {"limit": limit}
    if kind:
        params["kind"] = kind
    data = get_client().get(f"{API}/users/{email}/email-log", params=params)
    emit(data, fmt)


commands.append(users_email_log)


@click.command("users-ses-events")
@click.argument("email")
@click.option("--limit", type=int, default=50)
@click.option("--type", "event_type", default=None, help="Filter by event type.")
@format_option
def users_ses_events(email, limit, event_type, fmt):
    """Inbound SES events for a user."""
    params = {"limit": limit}
    if event_type:
        params["type"] = event_type
    data = get_client().get(f"{API}/users/{email}/ses-events", params=params)
    emit(data, fmt)


commands.append(users_ses_events)


# -- notes -------------------------------------------------------------------

@click.command("users-notes")
@click.argument("email")
@format_option
def users_notes(email, fmt):
    """List member notes for a user."""
    data = get_client().get(f"{API}/users/{email}/notes")
    emit(data, fmt)


commands.append(users_notes)


@click.command("users-notes-add")
@click.argument("email")
@click.option("--body", required=True, help="Note body text.")
@click.option("--kind", type=click.Choice([
    "action_item", "background", "general", "intake",
    "meeting", "persona", "recommendation", "source",
]), default="general")
@click.option("--visibility", type=click.Choice(["internal", "external"]), default="internal")
@click.option("--plan-id", type=int, default=None, help="Attach to a plan.")
@format_option
def users_notes_add(email, body, kind, visibility, plan_id, fmt):
    """Add a member note."""
    payload = {"user_email": email, "body": body, "kind": kind, "visibility": visibility}
    if plan_id is not None:
        payload["plan_id"] = plan_id
    data = get_client().post(f"{API}/member-notes", json_body=payload)
    emit(data, fmt)


commands.append(users_notes_add)


# -- activity / crm ----------------------------------------------------------

@click.command("users-activity")
@click.argument("email")
@click.option("--limit", type=int, default=50)
@click.option("--category", default=None)
@format_option
def users_activity(email, limit, category, fmt):
    """Activity context for a user."""
    params = {"limit": limit}
    if category:
        params["category"] = category
    data = get_client().get(f"{API}/users/{email}/activity", params=params)
    emit(data, fmt)


commands.append(users_activity)


@click.command("users-crm-record")
@click.argument("email")
@format_option
def users_crm_record(email, fmt):
    """CRM record for a user."""
    data = get_client().get(f"{API}/users/{email}/crm-record")
    emit(data, fmt)


commands.append(users_crm_record)


@click.command("users-payment-mismatches")
@format_option
def users_payment_mismatches(fmt):
    """List payment mismatches."""
    data = get_client().get(f"{API}/users/payment-mismatches")
    emit(data, fmt)


commands.append(users_payment_mismatches)
