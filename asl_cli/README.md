# asl-cli

Command-line client for the AI Shipping Labs production API. Wraps the full `/api` surface and the `/member-api/v1` member surface with typed subcommands.

## Install

```bash
uv sync
uv run asl --help
```

## Auth

Staff token resolved from: `ASL_API_TOKEN` env var -> `API_SHIPPING_LABS_API_TOKEN` in `.env` -> prompt.
Member key resolved from: `ASL_MEMBER_API_KEY` / `AI_SHIPPING_LABS_MEMBER_API_KEY`.
Base URL override: `ASL_BASE_URL` (default `https://aishippinglabs.com`).

## Usage

Commands are organized into nested groups. Use `--help` at any level:

```bash
uv run asl events --help            # subcommands: list, get, create, update, ...
uv run asl events create --help     # all flags for events create
```

### Flags instead of JSON

Create/update commands use individual `--flags`. You rarely need to write JSON:

```bash
uv run asl events create \
  --title "Office Hours" \
  --start-datetime "2026-08-01T17:00:00+02:00" \
  --status upcoming --published \
  --required-level open --create-zoom
```

Tier levels accept names: `open`, `registered`, `basic`, `main`, `premium` (or integers).
Lists accept comma-separated values: `--tags sprint:aug-2026,workshop`, `--host-ids 1,2`.

### Output formats

```bash
uv run asl events list --format table    # aligned table
uv run asl events list --format raw      # compact JSON (for piping)
uv run asl events list                   # pretty JSON (default)
```

### Escape hatch

For endpoints not yet wrapped:

```bash
uv run asl raw GET /api/events -p status=upcoming
uv run asl raw POST /api/integrations/settings --data '{"updates":[...]}'
```

## Command groups

`events`, `event-series`, `users`, `sprints`, `plans`, `contacts`, `campaigns`, `integrations`, `sync`, `worker`, `triggers`, `onboarding`, `redirects`, `utm-campaigns`, `hosts`, `articles`, `tier-reconcile`, `ses-events`, `crm-export`, `cleanup-gates`, `openapi`, `member-api`, `raw`
