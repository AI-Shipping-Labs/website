"""``asl plans`` -- plan CRUD, weeks, items, notes."""

from __future__ import annotations

import click

from asl_cli.commands._shared import emit, format_option, get_client, json_option

API = "/api"

# Flat one-row projection for ``--format table``. JSON/raw output stays the
# untouched API document so automation keeps the full structured result.
SEND_READY_COLUMNS = [
    "plan_id",
    "member_email",
    "sprint_slug",
    "shared_at",
    "status",
    "sent",
    "sent_at",
    "error",
]
SEND_READY_PUBLIC_ERROR = "Plan-ready delivery failed; retry the same action."


@click.group()
def plans():
    """Manage sprint plans."""


@plans.command("get")
@click.argument("plan_id", type=int)
@format_option
def plans_get(plan_id, fmt):
    """Get a single plan."""
    emit(get_client().get(f"{API}/plans/{plan_id}"), fmt)


@plans.command("send-ready")
@click.argument("plan_id", type=int)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Preview the readiness state without sending or writing anything.",
)
@format_option
@click.pass_context
def plans_send_ready(ctx, plan_id, dry_run, fmt):
    """Send the plan-ready email for exactly one plan.

    This is the idempotent default delivery: it shares the plan with its
    member, fires the bell notification and transactional email once, and
    reports ``already_sent`` or ``already_shared`` instead of notifying
    again. It never forces a re-send and never touches other plans in the
    sprint. To deliberately notify a member a second time, use the
    confirmed ``Re-share with member`` action on the Studio plan page.

    ``--dry-run`` reports the same structured outcome with no writes.
    """
    result = get_client().post(
        f"{API}/plans/{plan_id}/send-ready-email",
        json_body={"dry_run": True} if dry_run else {},
    )
    result = _safe_send_ready_result(result)
    ready = (result or {}).get("ready_email") or {}
    if fmt == "table":
        emit(_send_ready_table_row(result, ready), fmt, columns=SEND_READY_COLUMNS)
    else:
        emit(result, fmt)
    if ready.get("status") == "failed_retryable":
        click.echo(
            f"Plan-ready delivery failed for plan {plan_id}; the plan is "
            "still not shared. Retry the same command.",
            err=True,
        )
        ctx.exit(1)


def _send_ready_table_row(result, ready):
    result = result or {}
    return {
        "plan_id": result.get("plan_id"),
        "member_email": result.get("member_email"),
        "sprint_slug": result.get("sprint_slug"),
        "shared_at": result.get("shared_at"),
        "status": ready.get("status"),
        "sent": ready.get("sent"),
        "sent_at": ready.get("sent_at"),
        "error": ready.get("error"),
    }


def _safe_send_ready_result(result):
    """Keep provider exception details out of every CLI output format."""
    safe_result = dict(result or {})
    ready = dict(safe_result.get("ready_email") or {})
    if ready.get("status") == "failed_retryable":
        ready["error"] = SEND_READY_PUBLIC_ERROR
    safe_result["ready_email"] = ready
    return safe_result


@plans.command("move-unfinished")
@click.argument("plan_id", type=int)
@json_option("data", required=False)
@format_option
def plans_move_unfinished(plan_id, data, fmt):
    """Move unfinished items to the next sprint."""
    emit(get_client().post(f"{API}/plans/{plan_id}/move-unfinished", json_body=data), fmt)


@plans.command("draft-next-sprint")
@click.argument("plan_id", type=int)
@format_option
def plans_draft_next_sprint(plan_id, fmt):
    """AI-draft the next sprint plan."""
    emit(get_client().post(f"{API}/plans/{plan_id}/draft-next-sprint"), fmt)


@plans.command("weeks")
@click.argument("plan_id", type=int)
@format_option
def plans_weeks(plan_id, fmt):
    """List weeks for a plan."""
    emit(get_client().get(f"{API}/plans/{plan_id}/weeks"), fmt)


@plans.command("week")
@click.argument("week_id", type=int)
@format_option
def plans_week(week_id, fmt):
    """Get a single week."""
    emit(get_client().get(f"{API}/weeks/{week_id}"), fmt)


@plans.command("week-note")
@click.argument("week_id", type=int)
@json_option("data", required=False)
@format_option
def plans_week_note(week_id, data, fmt):
    """Get or update a week note."""
    if data:
        emit(get_client().patch(f"{API}/weeks/{week_id}/note", json_body=data), fmt)
    else:
        emit(get_client().get(f"{API}/weeks/{week_id}/note"), fmt)


@plans.command("create-checkpoint")
@click.argument("week_id", type=int)
@json_option("data", required=True)
@format_option
def plans_create_checkpoint(week_id, data, fmt):
    """Create a checkpoint in a week."""
    emit(get_client().post(f"{API}/weeks/{week_id}/checkpoints", json_body=data), fmt)


@plans.command("resources")
@click.argument("plan_id", type=int)
@format_option
def plans_resources(plan_id, fmt):
    """List resources for a plan."""
    emit(get_client().get(f"{API}/plans/{plan_id}/resources"), fmt)


@plans.command("deliverables")
@click.argument("plan_id", type=int)
@format_option
def plans_deliverables(plan_id, fmt):
    """List deliverables for a plan."""
    emit(get_client().get(f"{API}/plans/{plan_id}/deliverables"), fmt)


@plans.command("next-steps")
@click.argument("plan_id", type=int)
@format_option
def plans_next_steps(plan_id, fmt):
    """List next steps for a plan."""
    emit(get_client().get(f"{API}/plans/{plan_id}/next-steps"), fmt)


@plans.command("interview-notes")
@click.argument("plan_id", type=int)
@format_option
def plans_interview_notes(plan_id, fmt):
    """List interview notes for a plan."""
    emit(get_client().get(f"{API}/plans/{plan_id}/interview-notes"), fmt)


@plans.command("add-note")
@json_option("data", required=True)
@format_option
def plans_add_note(data, fmt):
    """Create an interview/member note."""
    emit(get_client().post(f"{API}/interview-notes", json_body=data), fmt)


@plans.command("get-note")
@click.argument("note_id", type=int)
@format_option
def plans_get_note(note_id, fmt):
    """Get a single note."""
    emit(get_client().get(f"{API}/interview-notes/{note_id}"), fmt)


@plans.command("update-note")
@click.argument("note_id", type=int)
@json_option("data", required=True)
@format_option
def plans_update_note(note_id, data, fmt):
    """Update a note."""
    emit(get_client().patch(f"{API}/interview-notes/{note_id}", json_body=data), fmt)


groups = [plans]
