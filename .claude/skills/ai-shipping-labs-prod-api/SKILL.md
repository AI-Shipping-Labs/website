---
name: ai-shipping-labs-prod-api
description: Foundational entrypoint for the AI Shipping Labs PRODUCTION HTTP API. Use to learn how to authenticate, where the token/config lives, how to discover the full endpoint surface via the OpenAPI spec, the safe-write protocol, and the cross-cutting surfaces — integration settings / env config, content sync, and background-task observability. For user/CRM endpoints use `ai-shipping-labs-users`; for events and workshops use `ai-shipping-labs-events`.
metadata:
  short-description: Auth, discovery, and cross-cutting surfaces of the AI Shipping Labs production API
---

# AI Shipping Labs Production API

The production site exposes a token-authenticated JSON API at `https://aishippinglabs.com/api/`. Use it to inspect and change prod state without prod DB or AWS access. Studio (the web UI) owns the same data; this API is the scriptable surface over it.

This is the foundational skill: authentication, config, endpoint discovery, the safe-write protocol, and the cross-cutting surfaces. For specific domains, read the specialized skills:

- Events and workshops — `ai-shipping-labs-events`.
- Users and CRM — `ai-shipping-labs-users`.

## Authentication

- Header: `Authorization: Token <key>` — the literal scheme is `Token`, NOT `Bearer`.
- The key is in the repo `.env` as `API_SHIPPING_LABS_API_TOKEN`. NEVER print the token, paste it into a file, an issue, a commit, or chat.
- The token authenticates as a staff user; missing/invalid token returns JSON `401`.
- Path style: no trailing slash (`/api/events`, not `/api/events/`).

### Use the `asl` CLI

All API calls go through the `asl` CLI (editable dev dependency in `asl_cli/`). It resolves the token from `.env` automatically — no manual `grep`/`curl` needed. Run from the repo root:

```bash
uv run asl --help
```

Every command group has its own help with subcommands and flags:

```bash
uv run asl events --help           # see subcommands (list, get, create, update, ...)
uv run asl events create --help    # see all create flags (--title, --start-datetime, --required-level, ...)
```

The CLI resolves the token from `ASL_API_TOKEN` env var, then `API_SHIPPING_LABS_API_TOKEN` in `.env`, then prompts. Override the base URL with `ASL_BASE_URL`.

If you need an endpoint the CLI does not yet wrap, use the escape hatch:

```bash
uv run asl raw GET /api/events -p status=upcoming
uv run asl raw POST /api/integrations/settings --data '{"updates":[...]}'
```

Tier levels (`--required-level`, `--target-min-level`) accept names, not just integers: `open` (0), `registered` (5), `basic` (10), `main` (20), `premium` (30).

## Discover the full surface: the OpenAPI spec

```bash
uv run asl openapi | python3 -c "import sys,json; d=json.load(sys.stdin); [print(m.upper(), p) for p,ops in sorted(d['paths'].items()) for m in ops]"
```

- Human Swagger UI: open `https://aishippinglabs.com/api/docs` in a browser (prompts for the token).
- Prefer reading the live spec when you need an exact request/response shape — it is generated from the code and never drifts.

## Safe-write protocol

For any mutation: `GET` the resource first to see current state, make the change, then `GET` again (or check a downstream effect) to confirm. Most writes are all-or-nothing and scrubbed of secret values in responses.

## Cross-cutting surfaces

### Integration settings (`asl integrations --help`)

Runtime config via the `IntegrationSetting` framework (DB override -> env -> django settings -> default). Studio's settings page writes the same rows.

- `asl integrations settings list` — lists keys with `group`, `configured` flag, and `source` enum. NEVER returns the actual value.
- `asl integrations settings set --updates KEY=VALUE,KEY2=VALUE2` — all-or-nothing batch. Empty value clears the DB override.

### Content sync (`asl sync --help`)

- `asl sync sources` — list registered content sources (with UUIDs).
- `asl sync trigger <uuid>` — kick off a sync (same as Studio "Force resync").

### Background tasks (`asl worker --help`)

- `asl worker tasks list` — recent tasks.
- `asl worker tasks failed` — failed tasks only.
- `asl worker tasks get <task_id>` — one task's detail.

Caveat: a task that catches its own error and returns `None` shows `success=True` with `result=None` — it will NOT appear in `failed`. Inspect `result` to spot silently-swallowed failures.

## Other surfaces

Sprints, plans, weeks, checkpoints, enrollments, certificates, redirects, tier overrides, onboarding, SES events — all have `asl` subcommands. Run `uv run asl --help` and search for the resource name.

## Notes

- The API runs against PRODUCTION. Treat writes as outward-facing prod changes — confirm intent, GET-before/after, and report what changed.
- New keys/endpoints appear automatically: the settings endpoint reads `integrations/settings_registry.py`, and the OpenAPI spec is generated from the routes. When unsure, fetch the spec.
