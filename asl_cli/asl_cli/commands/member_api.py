"""``asl member-api`` -- member-owned API surface (plans)."""

from __future__ import annotations

import click

from asl_cli.client import member_client
from asl_cli.commands._shared import emit, format_option, json_option

MEMBER_API = "/member-api/v1"


@click.group(name="member-api")
def member_api():
    """Member API (plans owned by the authenticated member)."""


@member_api.group("plans")
def member_plans():
    """Manage member plans."""


@member_plans.command("list")
@format_option
def member_api_plans(fmt):
    """List the authenticated member's plans."""
    emit(member_client().get(f"{MEMBER_API}/plans"), fmt)


@member_plans.command("get")
@click.argument("plan_id", type=int)
@format_option
def member_api_plan_get(plan_id, fmt):
    """Get a single plan."""
    emit(member_client().get(f"{MEMBER_API}/plans/{plan_id}"), fmt)


@member_plans.command("markdown")
@click.argument("plan_id", type=int)
@format_option
def member_api_plan_markdown(plan_id, fmt):
    """Download plan markdown (raw text)."""
    data = member_client().get(f"{MEMBER_API}/plans/{plan_id}/markdown", raw=True)
    click.echo(data)


@member_plans.command("progress")
@click.argument("plan_id", type=int)
@json_option("data", required=True,
             help_text='JSON with "checkpoints"/"deliverables"/"next_steps" arrays.')
@format_option
def member_api_plan_progress(plan_id, data, fmt):
    """Update progress on a plan."""
    emit(member_client().patch(f"{MEMBER_API}/plans/{plan_id}/progress", json_body=data), fmt)


groups = [member_api]
