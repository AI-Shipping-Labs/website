"""``asl onboarding`` -- read-only onboarding questionnaire surface."""

from __future__ import annotations

import click

from asl_cli.commands._shared import emit, format_option, get_client

API = "/api"

commands = []


@click.command("onboarding-questionnaires")
@format_option
def onboarding_questionnaires(fmt):
    """List onboarding questionnaires."""
    data = get_client().get(f"{API}/onboarding/questionnaires")
    emit(data, fmt)


commands.append(onboarding_questionnaires)


@click.command("onboarding-personas")
@format_option
def onboarding_personas(fmt):
    """List onboarding personas."""
    data = get_client().get(f"{API}/onboarding/personas")
    emit(data, fmt)


commands.append(onboarding_personas)


@click.command("onboarding-responses")
@format_option
def onboarding_responses(fmt):
    """List onboarding responses."""
    data = get_client().get(f"{API}/onboarding/responses")
    emit(data, fmt)


commands.append(onboarding_responses)


@click.command("onboarding-response-get")
@click.argument("email")
@format_option
def onboarding_response_get(email, fmt):
    """Get onboarding responses for a user."""
    data = get_client().get(f"{API}/onboarding/responses/{email}")
    emit(data, fmt)


commands.append(onboarding_response_get)
