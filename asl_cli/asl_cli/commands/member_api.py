"""``asl member-api`` -- member-owned API surface (plans)."""

from __future__ import annotations

import click

from asl_cli.client import member_client
from asl_cli.commands._shared import emit, format_option, json_arg

MEMBER_API = "/member-api/v1"

commands = []


@click.command("member-api-plans")
@format_option
def member_api_plans(fmt):
    """List the authenticated member's plans."""
    data = member_client().get(f"{MEMBER_API}/plans")
    emit(data, fmt)


commands.append(member_api_plans)


@click.command("member-api-plan-get")
@click.argument("plan_id", type=int)
@format_option
def member_api_plan_get(plan_id, fmt):
    """Get a single plan."""
    data = member_client().get(f"{MEMBER_API}/plans/{plan_id}")
    emit(data, fmt)


commands.append(member_api_plan_get)


@click.command("member-api-plan-markdown")
@click.argument("plan_id", type=int)
@format_option
def member_api_plan_markdown(plan_id, fmt):
    """Download plan markdown (raw text)."""
    data = member_client().get(f"{MEMBER_API}/plans/{plan_id}/markdown", raw=True)
    click.echo(data)


commands.append(member_api_plan_markdown)


@click.command("member-api-plan-progress")
@click.argument("plan_id", type=int)
@json_arg("data", required=True)
@format_option
def member_api_plan_progress(plan_id, data, fmt):
    """Update progress on a plan (JSON body with checkpoints/deliverables/next_steps)."""
    result = member_client().patch(f"{MEMBER_API}/plans/{plan_id}/progress", json_body=data)
    emit(result, fmt)


commands.append(member_api_plan_progress)
