"""Command modules for the asl CLI."""

from asl_cli.commands import (
    campaigns,
    contacts,
    event_series,
    events,
    integrations,
    misc,
    onboarding,
    plans,
    raw,
    redirects,
    sprints,
    sync,
    tier_overrides,
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
    misc,
    onboarding,
    plans,
    raw,
    redirects,
    sprints,
    sync,
    tier_overrides,
    triggers,
    users,
    utm_campaigns,
    worker,
]

groups = []
for _mod in _all_modules:
    groups.extend(getattr(_mod, "groups", []))
