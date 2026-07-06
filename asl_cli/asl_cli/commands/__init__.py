"""Command modules for the asl CLI."""

from asl_cli.commands import (
    campaigns,
    contacts,
    event_series,
    events,
    integrations,
    member_api,
    misc,
    onboarding,
    plans,
    raw,
    redirects,
    sprints,
    sync,
    triggers,
    users,
    utm_campaigns,
    worker,
)

# Module-level list so ``cli.py`` can iterate and register every command.
_all_modules = [
    campaigns,
    contacts,
    event_series,
    events,
    integrations,
    member_api,
    misc,
    onboarding,
    plans,
    raw,
    redirects,
    sprints,
    sync,
    triggers,
    users,
    utm_campaigns,
    worker,
]

# Collect ``commands`` attribute lists from each module (see any module below).
commands = []
for _mod in _all_modules:
    commands.extend(getattr(_mod, "commands", []))
