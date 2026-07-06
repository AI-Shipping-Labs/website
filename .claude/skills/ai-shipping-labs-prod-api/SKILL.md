---
name: ai-shipping-labs-prod-api
description: Foundational entrypoint for the AI Shipping Labs PRODUCTION HTTP API. Use to learn how to authenticate, where the token/config lives, how to discover the full endpoint surface via the OpenAPI spec, the safe-write protocol, and the cross-cutting surfaces — integration settings / env config, content sync, and background-task observability. For user/CRM endpoints use `ai-shipping-labs-users`; for events and workshops use `ai-shipping-labs-events`.
metadata:
  short-description: Auth, discovery, and cross-cutting surfaces of the AI Shipping Labs production API
---

# AI Shipping Labs Production API

The production site exposes a token-authenticated JSON API at `https://aishippinglabs.com/api/`. Use it to inspect and change prod state without prod DB or AWS access. Studio (the web UI) owns the same data; this API is the scriptable surface over it.

This is the foundational skill: authentication, config, endpoint discovery, the safe-write protocol, and the cross-cutting surfaces. For specific domains, read the specialized skills:

- Events and workshops — `ai-shipping-labs-events` (create/update events, event series, occurrences, Zoom meetings; workshop content sync).
- Users and CRM — `ai-shipping-labs-users` (look up users, tags, aliases, merge, notes, bounce handling).

## Authentication

- Header: `Authorization: Token <key>` — the literal scheme is `Token`, NOT `Bearer`.
- The key is in the repo `.env` as `API_SHIPPING_LABS_API_TOKEN`. NEVER print the token, paste it into a file, an issue, a commit, or chat. Read it inline at call time.
- The token authenticates as a staff user; missing/invalid token returns JSON `401`.
- Path style: no trailing slash (`/api/events`, not `/api/events/`).

### Use the `asl` CLI

All API calls should go through the `asl` CLI (editable dev dependency in `asl_cli/`). It handles token resolution from `.env` automatically — no manual `grep`/`curl` needed. Run from the repo root:

```bash
uv run asl events-list
```

The CLI resolves the token from `ASL_API_TOKEN` env var, then `API_SHIPPING_LABS_API_TOKEN` in `.env`, then prompts. Override the base URL with `ASL_BASE_URL`.

If you must call an endpoint the CLI does not yet wrap, use the escape hatch:

```bash
uv run asl raw GET /api/events -p status=upcoming
uv run asl raw POST /api/integrations/settings '{"updates":[...]}'
```

Run `uv run asl --help` for the full command list.

## Discover the full surface: the OpenAPI spec

The complete, always-current endpoint list lives in the generated OpenAPI document:

```bash
uv run asl openapi | python3 -c "import sys,json; d=json.load(sys.stdin); [print(m.upper(), p) for p,ops in sorted(d['paths'].items()) for m in ops]"
```

- Human Swagger UI: open `https://aishippinglabs.com/api/docs` in a browser (it prompts for the token).
- Prefer reading the live spec over this doc when you need an exact request/response shape — the spec is generated from the code and never drifts.

## Safe-write protocol

For any mutation: `GET` the resource first to see current state, `POST`/`PATCH` the change, then `GET` again (or check a downstream effect) to confirm. Most writes are all-or-nothing and scrubbed of secret values in responses.

## Cross-cutting surfaces

### Integration settings / env config (`/api/integrations/settings`)

This is how you set or inspect runtime config (the `IntegrationSetting` framework: DB override -> env -> django settings -> default). Studio's settings page writes the same rows.

- `GET /api/integrations/settings` — lists every registered key with `group`, `label`, `description`, `is_secret`, `is_boolean`, a `configured` flag, and a `source` enum (`db` / `env` / `django_settings` / `default` / `null`). It NEVER returns the actual value — only where it resolves from.
- `POST /api/integrations/settings` — body `{"updates": [{"key": "...", "value": "..."}, ...]}`. All-or-nothing: any key not in the registry fails the whole batch with `400 invalid_key`. Empty-string value on a non-boolean key CLEARS the DB override (deletes the row). Boolean keys accept `true`/`false` (JSON or string). Returns `{"status": "ok", "updated": N}`. The write clears the config cache so other workers pick it up. Response never echoes keys or values.

Inspect which keys are set and where they resolve from:

```bash
uv run asl integrations-settings-list --format table
```

Set one or more values:

```bash
uv run asl integrations-settings-set '{"updates":[{"key":"CONTENT_CDN_BASE","value":"https://cdn.aishippinglabs.com"}]}'
```

### Content sync (`/api/sync/sources`)

- `GET /api/sync/sources` — list registered content sources (with UUIDs).
- `POST /api/sync/sources/<uuid>/trigger` — kick off a sync for one source (same as the Studio "Force resync" button / the GitHub push webhook). Use this to make content/banner/workshop changes take effect in prod.

```bash
uv run asl sync-sources-list
uv run asl sync-source-trigger <uuid>
```

### Background task observability (`/api/worker/tasks`)

Invaluable for debugging async work (content sync, banner renders, emails, Zoom meeting creation):

- `GET /api/worker/tasks` — recent tasks with `name`, `success`, `result`, timing.
- `GET /api/worker/tasks/failed` — failed tasks only.
- `GET /api/worker/tasks/<task_id>` — one task's detail.

```bash
uv run asl worker-tasks-list --format table
uv run asl worker-tasks-failed
uv run asl worker-task-get <task_id>
```

Caveat: a task that catches its own error and returns `None` shows `success=True` with `result=None` — it will NOT appear in `failed`. Inspect `result` to spot silently-swallowed failures (this is exactly how a timed-out banner render hides).

## Other surfaces (see the spec for shapes)

These are not covered by a specialized skill; read the OpenAPI spec for exact request/response shapes before writing:

- Sprints + plans + weeks + checkpoints (`/api/sprints/...`, `/api/plans/...`).
- Enrollments + certificates (`/api/sprints/<slug>/enrollments`, `/api/courses/<slug>/...`).
- URL redirects (`/api/redirects`).
- Tier overrides (`/api/tier-overrides`).
- Onboarding read API (`/api/onboarding/...`).
- SES events (`/api/ses-events`, `/api/users/<email>/ses-events`).

All of these have `asl` subcommands — run `uv run asl --help` and search for the resource name.

## Notes

- The API runs against PRODUCTION. Treat writes as outward-facing prod changes — confirm intent, GET-before/after, and report what changed.
- New keys/endpoints appear automatically: the settings endpoint reads `integrations/settings_registry.py`, and the OpenAPI spec is generated from the routes. When unsure, fetch the spec.
- For endpoints not yet wrapped by the CLI, use `asl raw <METHOD> <path>`.
