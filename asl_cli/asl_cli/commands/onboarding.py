"""``asl onboarding`` -- read-only onboarding questionnaire surface."""

from __future__ import annotations

import click

from asl_cli.commands._shared import emit, format_option, get_client

API = "/api"


@click.group()
def onboarding():
    """Read onboarding data."""


@onboarding.command("questionnaires")
@format_option
def onboarding_questionnaires(fmt):
    """List onboarding questionnaires."""
    emit(get_client().get(f"{API}/onboarding/questionnaires"), fmt)


@onboarding.command("personas")
@format_option
def onboarding_personas(fmt):
    """List onboarding personas."""
    emit(get_client().get(f"{API}/onboarding/personas"), fmt)


@onboarding.command("responses")
@format_option
def onboarding_responses(fmt):
    """List onboarding responses."""
    emit(get_client().get(f"{API}/onboarding/responses"), fmt)


@onboarding.command("response")
@click.argument("email")
@format_option
def onboarding_response_get(email, fmt):
    """Get onboarding responses for a user."""
    emit(get_client().get(f"{API}/onboarding/responses/{email}"), fmt)


groups = [onboarding]
