---
name: ai-shipping-labs-prod-api
description: Use when asked to read or change something in AI Shipping Labs PRODUCTION via its HTTP API — e.g. set/inspect an integration setting or env config, create or update events/event-series, trigger a content sync, debug a background task, or look up a user. Covers auth, the OpenAPI spec, and the common endpoints.
metadata:
  short-description: Drive the AI Shipping Labs production API
---

# AI Shipping Labs Production API

The production site exposes a token-authenticated JSON API at `https://aishippinglabs.com/api/`. Use it to inspect and change prod state without prod DB or AWS access. Studio (the web UI) owns the same data; this API is the scriptable surface over it.

## Authentication

- Header: `Authorization: Token <key>` — the literal scheme is `Token`, NOT `Bearer`.
- The key is in the repo `.env` as `API_SHIPPING_LABS_API_TOKEN`. NEVER print the token, paste it into a file, an issue, a commit, or chat. Read it inline at call time.
- The token authenticates as a staff user; missing/invalid token returns JSON `401`.
- Path style: no trailing slash (`/api/events`, not `/api/events/`).

Read the token without echoing it:

```bash
cd /home/alexey/git/ai-shipping-labs
TOKEN=$(grep -E '^API_SHIPPING_LABS_API_TOKEN=' .env | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'" | tr -d '\r')
curl -s -H "Authorization: Token $TOKEN" https://aishippinglabs.com/api/events | python3 -m json.tool | head
```

## Discover the full surface: the OpenAPI spec

The complete, always-current endpoint list lives in the generated OpenAPI document. Fetch it with the token (the spec route accepts the same `Token` header):

```bash
curl -s -H "Authorization: Token $TOKEN" https://aishippinglabs.com/api/openapi.json \
  | python3 -c "import sys,json; d=json.load(sys.stdin); [print(m.upper(), p) for p,ops in sorted(d['paths'].items()) for m in ops]"
```

- Human Swagger UI: open `https://aishippinglabs.com/api/docs` in a browser (it prompts for the token).
- Prefer reading the live spec over this doc when you need an exact request/response shape — the spec is generated from the code and never drifts.

## Safe-write protocol

For any mutation: `GET` the resource first to see current state, `POST`/`PATCH` the change, then `GET` again (or check a downstream effect) to confirm. Most writes are all-or-nothing and scrubbed of secret values in responses.

## Common methods

### Integration settings / env config (`/api/integrations/settings`)

This is how you set or inspect runtime config (the `IntegrationSetting` framework: DB override -> env -> django settings -> default). Studio's settings page writes the same rows.

- `GET /api/integrations/settings` — lists every registered key with `group`, `label`, `description`, `is_secret`, `is_boolean`, a `configured` flag, and a `source` enum (`db` / `env` / `django_settings` / `default` / `null`). It NEVER returns the actual value — only where it resolves from.
- `POST /api/integrations/settings` — body `{"updates": [{"key": "...", "value": "..."}, ...]}`. All-or-nothing: any key not in the registry fails the whole batch with `400 invalid_key`. Empty-string value on a non-boolean key CLEARS the DB override (deletes the row). Boolean keys accept `true`/`false` (JSON or string). Returns `{"status": "ok", "updated": N}`. The write clears the config cache so other workers pick it up. Response never echoes keys or values.

Inspect which keys are set and where they resolve from:

```bash
curl -s -H "Authorization: Token $TOKEN" https://aishippinglabs.com/api/integrations/settings \
  | python3 -c "import sys,json;[print(f\"{e['key']:38} configured={e['configured']} source={e['source']}\") for e in json.load(sys.stdin)['settings']]"
```

Set one or more values (example — CDN base + a content key):

```bash
curl -s -X POST -H "Authorization: Token $TOKEN" -H "Content-Type: application/json" \
  https://aishippinglabs.com/api/integrations/settings \
  -d '{"updates":[{"key":"CONTENT_CDN_BASE","value":"https://cdn.aishippinglabs.com"}]}'
```

To set secret values (tokens, URLs) without exposing them in your shell history or the command line, read them from a source file/env into the JSON body via a small `python3` heredoc rather than inlining the literal.

### Events (`/api/events`)

- `GET /api/events` — list. `GET /api/events/<slug>` — detail.
- `POST /api/events` — create. `PATCH /api/events/<slug>` — update. See the spec for the field shape (title, dates, platform, gating level, status, etc.).

### Event series (`/api/event-series`)

- `GET/POST /api/event-series`, `GET/PATCH /api/event-series/<id>`.
- `POST /api/event-series/<id>/occurrences/bulk` — bulk create/manage occurrences.

### Content sync (`/api/sync/sources`)

- `GET /api/sync/sources` — list registered content sources (with UUIDs).
- `POST /api/sync/sources/<uuid>/trigger` — kick off a sync for one source (same as the Studio "Force resync" button / the GitHub push webhook). Use this to make content/banner changes take effect in prod.

### Background task observability (`/api/worker/tasks`)

Invaluable for debugging async work (content sync, banner renders, emails):

- `GET /api/worker/tasks` — recent tasks with `name`, `success`, `result`, timing.
- `GET /api/worker/tasks/failed` — failed tasks only.
- `GET /api/worker/tasks/<task_id>` — one task's detail.

Caveat: a task that catches its own error and returns `None` shows `success=True` with `result=None` — it will NOT appear in `failed`. Inspect `result` to spot silently-swallowed failures (this is exactly how a timed-out banner render hides).

### Other surfaces (see the spec for shapes)

Users (`/api/users`, tags, aliases, merge, mark-bounced), sprints + plans + weeks + checkpoints (`/api/sprints/...`, `/api/plans/...`), enrollments + certificates (`/api/sprints/<slug>/enrollments`, `/api/courses/<slug>/...`), contacts import/export (`/api/contacts/...`), URL redirects (`/api/redirects`), tier overrides (`/api/tier-overrides`), onboarding read API (`/api/onboarding/...`), SES events (`/api/ses-events`).

## Notes

- The API runs against PRODUCTION. Treat writes as outward-facing prod changes — confirm intent, GET-before/after, and report what changed.
- New keys/endpoints appear automatically: the settings endpoint reads `integrations/settings_registry.py`, and the OpenAPI spec is generated from the routes. When unsure, fetch the spec.
