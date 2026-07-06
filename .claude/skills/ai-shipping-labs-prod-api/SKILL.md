---
name: ai-shipping-labs-prod-api
description: Foundational entrypoint for the AI Shipping Labs PRODUCTION HTTP API. Use to learn how to authenticate, where the token/config lives, how to discover the full endpoint surface via the OpenAPI spec, the safe-write protocol, and the cross-cutting surfaces — integration settings / env config, content sync, and background-task observability. For user/CRM endpoints use `ai-shipping-labs-users`; for events and workshops use `ai-shipping-labs-events`.
metadata:
  short-description: Auth, discovery, and cross-cutting surfaces of the AI Shipping Labs production API
---

# AI Shipping Labs Production API

The production site exposes a token-authenticated JSON API at `https://aishippinglabs.com/api/`. Use it to inspect and change prod state without prod DB or AWS access. Studio (the web UI) owns the same data; this API is the scriptable surface over it.

Specialized skills: `ai-shipping-labs-events`, `ai-shipping-labs-users`.

## Authentication

- Header: `Authorization: Token <key>` (literal scheme `Token`).
- Key is in `.env` as `API_SHIPPING_LABS_API_TOKEN`. NEVER print it.
- Staff token; missing/invalid returns JSON `401`. No trailing slashes on paths.

### Use the `asl` CLI

All API calls go through the `asl` CLI (editable dev dependency in `asl_cli/`). It resolves the token from `.env` automatically.

```bash
uv run asl --help                 # all command groups
uv run asl <group> --help         # commands in a group
uv run asl <group> <command> --help  # flags for a command
```

Token resolution: `ASL_API_TOKEN` env var -> `API_SHIPPING_LABS_API_TOKEN` in `.env` -> prompt. Override base URL with `ASL_BASE_URL`.

Escape hatch for unwrapped endpoints:

```bash
uv run asl raw GET /api/events -p status=upcoming
```

Tier levels (`--required-level`, `--target-min-level`) accept names: `open` (0, everyone), `registered` (5, any logged-in), `basic` (10, Basic+), `main` (20, Main+), `premium` (30, Premium only). Also accept raw integers.

## Discovering the surface

```bash
uv run asl openapi | python3 -c "import sys,json; d=json.load(sys.stdin); [print(m.upper(), p) for p,ops in sorted(d['paths'].items()) for m in ops]"
```

Swagger UI: `https://aishippinglabs.com/api/docs` (prompts for token).

## Safe-write protocol

GET the resource first, make the change, then GET again to confirm. Most writes are all-or-nothing and scrub secrets from responses.

## Cross-cutting surfaces

Run `uv run asl <group> --help` for exact flags. Key commands:

### Integration settings (`asl integrations --help`)

- `asl integrations settings` — list keys with group, source, configured flag. Never returns values.
- `asl integrations set --updates KEY=VALUE,KEY2=VALUE2` — all-or-nothing batch.

### Content sync (`asl sync --help`)

- `asl sync sources` — list content sources (with UUIDs).
- `asl sync trigger <uuid>` — force resync.
- `asl sync plan-sprints [--since ...] [--dry-run]` — Slack plan-sprints ingestion.

### Background tasks (`asl worker --help`)

- `asl worker tasks` — recent tasks.
- `asl worker failed-tasks` — failed only.
- `asl worker task <task_id>` — one task's detail.

A task that catches its own error returns `success=True` with `result=None` — it will NOT appear in failed-tasks. Inspect `result`.

## Notes

- The API runs against PRODUCTION. Treat writes as prod changes.
- New endpoints appear automatically in the OpenAPI spec. When unsure, fetch it.
