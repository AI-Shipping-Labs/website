"""Command modules for the asl CLI.

Each module exposes a ``groups`` list of top-level click.Group (or
click.Command) objects. The CLI entry point iterates and registers them.
"""

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

groups = []
for _mod in _all_modules:
    groups.extend(getattr(_mod, "groups", []))
