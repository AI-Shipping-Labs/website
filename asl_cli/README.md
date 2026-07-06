# asl-cli

Command-line client for the AI Shipping Labs production API. Wraps the full `/api` surface and the `/member-api/v1` member surface so skills, operators, and scripts call `asl <command>` instead of raw `curl`.

## Install

The package is an editable dev dependency of the main project. From the repo root:

```bash
uv sync
uv run asl --help
```

## Auth

The staff API token is resolved in order:

1. `ASL_API_TOKEN` environment variable.
2. `API_SHIPPING_LABS_API_TOKEN` in the repo `.env` file.
3. Interactive prompt (TTY only).

Member API commands (`asl member-api ...`) use `ASL_MEMBER_API_KEY` / `AI_SHIPPING_LABS_MEMBER_API_KEY` analogously.

Override the base URL with `ASL_BASE_URL` (defaults to `https://aishippinglabs.com`).

## Usage

Default output is pretty-printed JSON. Use `--format table` for list endpoints or `--format raw` for compact JSON.

```bash
# List events
uv run asl events-list --format table

# Get a single user
uv run asl users-get someone@example.com

# Search users
uv run asl users-list -q alexey --format table

# Create an event
uv run asl events-create '{"title":"Test","start_datetime":"2026-08-01T17:00:00+02:00","status":"draft"}'

# Read JSON body from a file
uv run asl events-create @event-body.json

# Generic escape hatch for any endpoint
uv run asl raw GET /api/events -p status=upcoming
uv run asl raw POST /api/integrations/settings '{"updates":[{"key":"X","value":"Y"}]}'
```

## Commands

Staff API (`/api`): users, events, event-series, plans, sprints, contacts, campaigns, integrations, sync, worker, triggers, onboarding, redirects, utm-campaigns, hosts, articles, tier-reconcile, tier-overrides, ses-events, crm-export, cleanup-gates.

Member API (`/member-api/v1`): plans list/get/markdown/progress.

Run `asl --help` for the full command list.
