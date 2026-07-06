"""``asl plans`` -- plan CRUD, weeks, items, notes."""

from __future__ import annotations

import click

from asl_cli.commands._shared import emit, format_option, get_client, json_option

API = "/api"


@click.group()
def plans():
    """Manage sprint plans."""


@plans.command("get")
@click.argument("plan_id", type=int)
@format_option
def plans_get(plan_id, fmt):
    """Get a single plan."""
    emit(get_client().get(f"{API}/plans/{plan_id}"), fmt)


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


# -- weeks (nested) ----------------------------------------------------------

@click.group(name="weeks")
def plans_weeks():
    """List weeks for a plan."""


@plans_weeks.command("list")
@click.argument("plan_id", type=int)
@format_option
def plan_weeks_list(plan_id, fmt):
    """List weeks for a plan."""
    emit(get_client().get(f"{API}/plans/{plan_id}/weeks"), fmt)


@plans_weeks.command("get")
@click.argument("week_id", type=int)
@format_option
def week_get(week_id, fmt):
    """Get a single week."""
    emit(get_client().get(f"{API}/weeks/{week_id}"), fmt)


@plans_weeks.command("note")
@click.argument("week_id", type=int)
@json_option("data", required=False)
@format_option
def week_note(week_id, data, fmt):
    """Get or update a week note."""
    if data:
        emit(get_client().patch(f"{API}/weeks/{week_id}/note", json_body=data), fmt)
    else:
        emit(get_client().get(f"{API}/weeks/{week_id}/note"), fmt)


@plans_weeks.command("create-checkpoint")
@click.argument("week_id", type=int)
@json_option("data", required=True)
@format_option
def week_checkpoint_create(week_id, data, fmt):
    """Create a checkpoint in a week."""
    emit(get_client().post(f"{API}/weeks/{week_id}/checkpoints", json_body=data), fmt)


plans.add_command(plans_weeks)


# -- items (nested) ----------------------------------------------------------

@click.group(name="items")
def plans_items():
    """List resources / deliverables / next-steps."""


@plans_items.command("resources")
@click.argument("plan_id", type=int)
@format_option
def plan_resources(plan_id, fmt):
    """List resources for a plan."""
    emit(get_client().get(f"{API}/plans/{plan_id}/resources"), fmt)


@plans_items.command("deliverables")
@click.argument("plan_id", type=int)
@format_option
def plan_deliverables(plan_id, fmt):
    """List deliverables for a plan."""
    emit(get_client().get(f"{API}/plans/{plan_id}/deliverables"), fmt)


@plans_items.command("next-steps")
@click.argument("plan_id", type=int)
@format_option
def plan_next_steps(plan_id, fmt):
    """List next steps for a plan."""
    emit(get_client().get(f"{API}/plans/{plan_id}/next-steps"), fmt)


@plans_items.command("interview-notes")
@click.argument("plan_id", type=int)
@format_option
def plan_interview_notes(plan_id, fmt):
    """List interview notes for a plan."""
    emit(get_client().get(f"{API}/plans/{plan_id}/interview-notes"), fmt)


plans.add_command(plans_items)


# -- notes (nested) ----------------------------------------------------------

@click.group(name="notes")
def plans_notes():
    """Manage interview / member notes."""


@plans_notes.command("create")
@json_option("data", required=True)
@format_option
def interview_note_create(data, fmt):
    """Create an interview/member note."""
    emit(get_client().post(f"{API}/interview-notes", json_body=data), fmt)


@plans_notes.command("get")
@click.argument("note_id", type=int)
@format_option
def interview_note_get(note_id, fmt):
    """Get a single note."""
    emit(get_client().get(f"{API}/interview-notes/{note_id}"), fmt)


@plans_notes.command("update")
@click.argument("note_id", type=int)
@json_option("data", required=True)
@format_option
def interview_note_update(note_id, data, fmt):
    """Update a note."""
    emit(get_client().patch(f"{API}/interview-notes/{note_id}", json_body=data), fmt)


plans.add_command(plans_notes)


groups = [plans]
