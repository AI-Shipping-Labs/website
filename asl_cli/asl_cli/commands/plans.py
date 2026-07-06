"""``asl plans`` -- plan CRUD, weeks, items, sprint plan actions."""

from __future__ import annotations

import click

from asl_cli.commands._shared import emit, format_option, get_client, json_arg

API = "/api"

commands = []


# -- plan detail / actions ---------------------------------------------------

@click.command("plans-get")
@click.argument("plan_id", type=int)
@format_option
def plans_get(plan_id, fmt):
    """Get a single plan."""
    data = get_client().get(f"{API}/plans/{plan_id}")
    emit(data, fmt)


commands.append(plans_get)


@click.command("plans-move-unfinished")
@click.argument("plan_id", type=int)
@json_arg("data", required=False)
@format_option
def plans_move_unfinished(plan_id, data, fmt):
    """Move unfinished items to the next sprint."""
    result = get_client().post(f"{API}/plans/{plan_id}/move-unfinished", json_body=data)
    emit(result, fmt)


commands.append(plans_move_unfinished)


@click.command("plans-draft-next-sprint")
@click.argument("plan_id", type=int)
@format_option
def plans_draft_next_sprint(plan_id, fmt):
    """AI-draft the next sprint plan."""
    data = get_client().post(f"{API}/plans/{plan_id}/draft-next-sprint")
    emit(data, fmt)


commands.append(plans_draft_next_sprint)


# -- sprint-plan collection --------------------------------------------------

@click.command("sprint-plans-list")
@click.argument("sprint_slug")
@format_option
def sprint_plans_list(sprint_slug, fmt):
    """List plans for a sprint."""
    data = get_client().get(f"{API}/sprints/{sprint_slug}/plans")
    emit(data, fmt)


commands.append(sprint_plans_list)


@click.command("sprint-plans-bulk-import")
@click.argument("sprint_slug")
@json_arg("data", required=True)
@format_option
def sprint_plans_bulk_import(sprint_slug, data, fmt):
    """Bulk-import plans for a sprint (JSON body)."""
    result = get_client().post(f"{API}/sprints/{sprint_slug}/plans/bulk-import", json_body=data)
    emit(result, fmt)


commands.append(sprint_plans_bulk_import)


@click.command("sprint-plans-send-ready-emails")
@click.argument("sprint_slug")
@format_option
def sprint_plans_send_ready_emails(sprint_slug, fmt):
    """Send ready-plan emails for a sprint."""
    data = get_client().post(f"{API}/sprints/{sprint_slug}/plans/send-ready-emails")
    emit(data, fmt)


commands.append(sprint_plans_send_ready_emails)


@click.command("sprint-partner-intro-emails")
@click.argument("sprint_slug")
@format_option
def sprint_partner_intro_emails(sprint_slug, fmt):
    """Send accountability partner intro emails for a sprint."""
    data = get_client().post(f"{API}/sprints/{sprint_slug}/partner-intro-emails")
    emit(data, fmt)


commands.append(sprint_partner_intro_emails)


# -- weeks -------------------------------------------------------------------

@click.command("plan-weeks")
@click.argument("plan_id", type=int)
@format_option
def plan_weeks(plan_id, fmt):
    """List weeks for a plan."""
    data = get_client().get(f"{API}/plans/{plan_id}/weeks")
    emit(data, fmt)


commands.append(plan_weeks)


@click.command("week-get")
@click.argument("week_id", type=int)
@format_option
def week_get(week_id, fmt):
    """Get a single week."""
    data = get_client().get(f"{API}/weeks/{week_id}")
    emit(data, fmt)


commands.append(week_get)


@click.command("week-note")
@click.argument("week_id", type=int)
@json_arg("data", required=False)
@format_option
def week_note(week_id, data, fmt):
    """Get or update a week note."""
    if data:
        result = get_client().patch(f"{API}/weeks/{week_id}/note", json_body=data)
    else:
        result = get_client().get(f"{API}/weeks/{week_id}/note")
    emit(result, fmt)


commands.append(week_note)


# -- checkpoints / resources / deliverables / next steps --------------------

@click.command("week-checkpoints-create")
@click.argument("week_id", type=int)
@json_arg("data", required=True)
@format_option
def week_checkpoints_create(week_id, data, fmt):
    """Create a checkpoint in a week (JSON body)."""
    result = get_client().post(f"{API}/weeks/{week_id}/checkpoints", json_body=data)
    emit(result, fmt)


commands.append(week_checkpoints_create)


@click.command("checkpoint-get")
@click.argument("checkpoint_id", type=int)
@format_option
def checkpoint_get(checkpoint_id, fmt):
    """Get or update a checkpoint."""
    data = get_client().get(f"{API}/checkpoints/{checkpoint_id}")
    emit(data, fmt)


commands.append(checkpoint_get)


@click.command("checkpoint-update")
@click.argument("checkpoint_id", type=int)
@json_arg("data", required=True)
@format_option
def checkpoint_update(checkpoint_id, data, fmt):
    """Update a checkpoint (JSON body)."""
    result = get_client().patch(f"{API}/checkpoints/{checkpoint_id}", json_body=data)
    emit(result, fmt)


commands.append(checkpoint_update)


@click.command("checkpoint-move")
@click.argument("checkpoint_id", type=int)
@json_arg("data", required=True)
@format_option
def checkpoint_move(checkpoint_id, data, fmt):
    """Move a checkpoint (JSON body)."""
    result = get_client().post(f"{API}/checkpoints/{checkpoint_id}/move", json_body=data)
    emit(result, fmt)


commands.append(checkpoint_move)


@click.command("plan-resources")
@click.argument("plan_id", type=int)
@format_option
def plan_resources(plan_id, fmt):
    """List resources for a plan."""
    data = get_client().get(f"{API}/plans/{plan_id}/resources")
    emit(data, fmt)


commands.append(plan_resources)


@click.command("resource-get")
@click.argument("item_id", type=int)
@format_option
def resource_get(item_id, fmt):
    """Get a resource."""
    data = get_client().get(f"{API}/resources/{item_id}")
    emit(data, fmt)


commands.append(resource_get)


@click.command("plan-deliverables")
@click.argument("plan_id", type=int)
@format_option
def plan_deliverables(plan_id, fmt):
    """List deliverables for a plan."""
    data = get_client().get(f"{API}/plans/{plan_id}/deliverables")
    emit(data, fmt)


commands.append(plan_deliverables)


@click.command("deliverable-get")
@click.argument("item_id", type=int)
@format_option
def deliverable_get(item_id, fmt):
    """Get a deliverable."""
    data = get_client().get(f"{API}/deliverables/{item_id}")
    emit(data, fmt)


commands.append(deliverable_get)


@click.command("plan-next-steps")
@click.argument("plan_id", type=int)
@format_option
def plan_next_steps(plan_id, fmt):
    """List next steps for a plan."""
    data = get_client().get(f"{API}/plans/{plan_id}/next-steps")
    emit(data, fmt)


commands.append(plan_next_steps)


@click.command("next-step-get")
@click.argument("item_id", type=int)
@format_option
def next_step_get(item_id, fmt):
    """Get a next step."""
    data = get_client().get(f"{API}/next-steps/{item_id}")
    emit(data, fmt)


commands.append(next_step_get)


# -- interview notes ---------------------------------------------------------

@click.command("plan-interview-notes")
@click.argument("plan_id", type=int)
@format_option
def plan_interview_notes(plan_id, fmt):
    """List interview notes for a plan."""
    data = get_client().get(f"{API}/plans/{plan_id}/interview-notes")
    emit(data, fmt)


commands.append(plan_interview_notes)


@click.command("interview-note-create")
@json_arg("data", required=True)
@format_option
def interview_note_create(data, fmt):
    """Create an interview/member note (JSON body)."""
    result = get_client().post(f"{API}/interview-notes", json_body=data)
    emit(result, fmt)


commands.append(interview_note_create)


@click.command("interview-note-get")
@click.argument("note_id", type=int)
@format_option
def interview_note_get(note_id, fmt):
    """Get a single interview/member note."""
    data = get_client().get(f"{API}/interview-notes/{note_id}")
    emit(data, fmt)


commands.append(interview_note_get)


@click.command("interview-note-update")
@click.argument("note_id", type=int)
@json_arg("data", required=True)
@format_option
def interview_note_update(note_id, data, fmt):
    """Update an interview/member note (JSON body)."""
    result = get_client().patch(f"{API}/interview-notes/{note_id}", json_body=data)
    emit(result, fmt)


commands.append(interview_note_update)
