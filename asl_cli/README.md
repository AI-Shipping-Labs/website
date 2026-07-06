# asl-cli

Command-line client for the AI Shipping Labs production API. Admin/operator tool wrapping the full `/api` surface with typed subcommands.

## Install

```bash
uv sync
uv run asl --help
```

## Auth

Staff token resolved from: `ASL_API_TOKEN` env var -> `API_SHIPPING_LABS_API_TOKEN` in `.env` -> prompt.
Override base URL with `ASL_BASE_URL` (default `https://aishippinglabs.com`).

## Usage

Commands are organized into groups, max 2 levels: `asl <group> <command>`. Use `--help` at any level:

```bash
uv run asl events --help
uv run asl events create --help
```

### Flags instead of JSON

Create/update commands use individual `--flags`:

```bash
uv run asl events create \
  --title "Office Hours" \
  --start-datetime "2026-08-01T17:00:00+02:00" \
  --required-level open --create-zoom
```

Tier levels accept names: `open` (0), `registered` (5), `basic` (10), `main` (20), `premium` (30). Also integers.
Lists accept comma-separated values: `--tags sprint:aug-2026,workshop`, `--host-ids 1,2`.

### Output formats

```bash
uv run asl events list --format table    # aligned table
uv run asl events list --format raw      # compact JSON (for piping)
uv run asl events list                   # pretty JSON (default)
```

### Escape hatch

```bash
uv run asl raw GET /api/events -p status=upcoming
uv run asl raw POST /api/integrations/settings --data '{"updates":[...]}'
```

## Command groups

`events`, `event-series`, `users`, `sprints`, `plans`, `contacts`, `tier-overrides`, `campaigns`, `integrations`, `sync`, `worker`, `triggers`, `onboarding`, `redirects`, `utm-campaigns`, `hosts`, `articles`, `tier-reconcile`, `ses-events`, `crm-export`, `cleanup-gates`, `openapi`, `raw`
