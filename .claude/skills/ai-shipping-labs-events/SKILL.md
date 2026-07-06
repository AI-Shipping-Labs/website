---
name: ai-shipping-labs-events
description: Create and manage AI Shipping Labs events and workshops in production. Use when asked to "create an event", "schedule a workshop", "set up an event series", "add a Zoom event", "make a recurring event", "publish a workshop", cancel/reschedule an occurrence, or bulk-create Zoom meetings for a series. Events are created via the production API; workshops are git content synced from the workshops-content repo.
metadata:
  short-description: Create and manage events, event series, and workshops
---

# AI Shipping Labs Events and Workshops

Two distinct surfaces, do not confuse them:

- Events and event series — created and edited through the production HTTP API (Studio-owned). Server-assigned ids/slugs are fine.
- Workshops — markdown in the `AI-Shipping-Labs/workshops-content` git repo, synced via the content pipeline. NOT created through the events API.

## Auth

All API calls use the `asl` CLI, which resolves the token from `.env` automatically. See `ai-shipping-labs-prod-api` for full auth, base URL, and safe-write protocol. GET-before, write, GET-after.

## Events

- `asl events-list [--status draft|upcoming|completed|cancelled]` — list.
- `asl events-get <slug>` — detail.
- `asl events-create '<json>'` — create a Studio-origin event.
- `asl events-update <slug> '<json>'` — update.

GitHub-origin / synced events are inspectable but read-only here (`editable: false` in the serialized object); only Studio/API-origin events can be created and patched. Studio source means server-assigned ids/slugs are fine.

### Event fields

Required on create: `title`, `start_datetime`.

| Field | Type | Notes |
| --- | --- | --- |
| `title` | string | Required. |
| `slug` | string | Optional. Auto-derived from `title` if omitted; capped at 70 chars. |
| `description` | string | Plain text; `description_html` is rendered server-side. |
| `kind` | string | `standard` / `workshop` / `meetup` / `q_and_a`. Default `standard`. |
| `platform` | string | `zoom` or `custom`. Default `zoom`. |
| `start_datetime` | date-time | Required. ISO 8601. Naive values are made aware in the server's current timezone, so pass an explicit offset. |
| `end_datetime` | date-time | Optional. ISO 8601. |
| `timezone` | string | IANA name, e.g. `Europe/Berlin`. Display timezone for the event. |
| `zoom_join_url` | string | Join URL for Zoom or custom-platform events. |
| `required_level` | integer | Access gate: `0` open, `5` registered, `10` basic, `20` main, `30` premium. Default `0`. |
| `status` | string | `draft` / `upcoming` / `completed` / `cancelled`. |
| `published` | boolean | Publish flag. Setting it true stamps `published_at`. |
| `external_host` | string | Partner host pill: `''` (community), `Maven`, `Luma`, `DataTalksClub`. |
| `host_email` | string | Optional. Email address of a platform user who should be auto-registered as the event host attendee. Blank or non-resolvable emails do not create a host registration. |
| `host_ids` | array of integers | Optional on create/update. Ordered event-host ids shown on the public event detail page. Empty array clears all event hosts. Host contact emails are display-only and do not receive calendar invites. |
| `tags` | array of strings | Free-form, e.g. `["sprint:may-2026"]`. |
| `create_zoom` | boolean | Write-only. When `true` and `platform` is `zoom`, provisions a real Zoom meeting and populates `zoom_join_url` / `zoom_meeting_id`. Idempotent (no-op if a meeting already exists). Not returned in responses. |
| `generate_banner` | boolean | Write-only. Defaults to `true` on create — the API auto-generates the 1200x630 social/banner image in the background. Set `false` to skip. On `PATCH` it only re-renders when explicitly `true`. Not returned in responses. |
| `banner_url` | string | Read-only. The resolved effective banner / social image URL echoed in responses (precedence: frontmatter cover, then Studio custom upload, then generated). Empty until the async render finishes. |

`draft` and `cancelled` are hidden from public visitors; `upcoming` and `completed` are public. To make an event visible you generally set `status: upcoming` and `published: true`.

### Host auto-registration

Creating (or publishing) a non-draft upcoming event via the API auto-registers the platform user resolved from `host_email` as a normal event attendee. That host receives the normal `event_registration` email, `.ics` attachment, reminders, and reschedule notices through the same registration paths as attendees; the registration and reschedule emails include host-only management links for the resolved host registration. `host_ids` and `Host.email` are only for public event-host display/contact info and are never used for email delivery. Blank or non-resolvable `host_email` values do not create a registration or send email; the save still succeeds.

### Create example

```bash
uv run asl events-create '{
  "title": "Office Hours: May 5",
  "description": "Open Q&A.",
  "kind": "standard",
  "platform": "zoom",
  "start_datetime": "2026-05-05T17:00:00+02:00",
  "end_datetime": "2026-05-05T18:00:00+02:00",
  "timezone": "Europe/Berlin",
  "required_level": 0,
  "status": "upcoming",
  "published": true,
  "host_email": "host@example.com",
  "host_ids": [1, 2],
  "tags": ["sprint:may-2026"]
}'
```

Update via slug, e.g. cancel an event:

```bash
uv run asl events-update office-hours-2026-05-05 '{"status": "cancelled"}'
```

Create a Zoom event and provision a real Zoom meeting in one call by adding `"create_zoom": true` (only valid when `platform` is `zoom`):

```bash
uv run asl events-create '{
  "title": "Office Hours: May 5",
  "platform": "zoom",
  "start_datetime": "2026-05-05T17:00:00+02:00",
  "status": "upcoming",
  "published": true,
  "create_zoom": true
}'
```

The create-event call is the primary action and never rolls back on a Zoom problem: if Zoom is unconfigured or its API fails, the event is still created (`201`/`200`) and the response carries a non-fatal `zoom_error` string instead of a join URL. Because `create_zoom` is idempotent, you can safely retry later with `"create_zoom": true` via `asl events-update` once Zoom is working.

## Event series

A series is the recurring template; its occurrences are individual `Event` rows linked back to it (`event_series` FK, `series_position`). The series carries the cadence; the occurrences carry the actual datetimes.

- `asl event-series-list` — list. `asl event-series-get <id>` — detail.
- `asl event-series-create '<json>'` — create.
- `asl event-series-update <id> '<json>'` — update.
- `asl event-series-occurrences-bulk <id> '<json>'` — ADD missing occurrences (additive; never deletes).
- `asl event-series-occurrences-reconcile <id> '<json>'` — EXACT-SET: declares the full desired occurrence set in one atomic call (creates missing, reuses existing, cancels extras).

Series create fields — required: `name`, `day_of_week`, `start_time`.

| Field | Type | Notes |
| --- | --- | --- |
| `name` | string | Required. |
| `slug` | string | Optional. |
| `cadence` | string | Only `weekly` is currently supported. |
| `day_of_week` | integer | Required. `0`=Monday through `6`=Sunday. |
| `start_time` | string | Required. `HH:MM` or `HH:MM:SS`. |
| `timezone` | string | IANA name. |
| `required_level` | integer | Same tier-level enum as events. Occurrences must match the series level (guardrail enforced on bulk/PATCH). |
| `is_active` | boolean | Whether the series is active. |

Create a weekly series, then add its occurrences:

```bash
SERIES=$(uv run asl event-series-create '{"name":"Weekly Office Hours","cadence":"weekly","day_of_week":1,"start_time":"17:00:00","timezone":"Europe/Berlin","required_level":0}')
SERIES_ID=$(echo "$SERIES" | python3 -c "import sys,json;print(json.load(sys.stdin)['id'])")

uv run asl event-series-occurrences-bulk "$SERIES_ID" '{"occurrences":[
  {"start_datetime":"2026-05-05T17:00:00+02:00"},
  {"start_datetime":"2026-05-12T17:00:00+02:00"},
  {"start_datetime":"2026-05-19T17:00:00+02:00"}
]}'
```

Cancel one occurrence with `asl events-update <slug> '{"status": "cancelled"}'` — this can send cancellation emails to registrants.

## Zoom meetings

Zoom is the default `platform`. There are two wired auto-creation paths.

Single event — pass `"create_zoom": true` on create/update. When the event's `platform` is `zoom` and it has no existing `zoom_meeting_id`, the API calls the same `create_meeting` service the series path uses and stores `zoom_meeting_id` + `zoom_join_url`. It is idempotent (re-requesting on an event that already has a meeting is a no-op, never an overwrite) and fails soft (a Zoom outage still returns the event with a `zoom_error` key).

Series (bulk) — the explicit series action remains the path for provisioning every occurrence at once:

- `asl event-series-zoom-meetings <id> [--dry-run]` — create a Zoom meeting for every eligible occurrence in the series. Eligible = future (`is_upcoming`), `platform == "zoom"`, and no existing `zoom_meeting_id`. Past / cancelled / draft / `custom`-platform occurrences are skipped.
- `--dry-run` returns a preview with no Zoom calls or enqueues.
- A real run enqueues a background worker and returns `200` with a `status` of `enqueued` (or `noop` when nothing is eligible) plus a `task_id`.
- It is idempotent per occurrence: occurrences that already carry a `zoom_meeting_id` are skipped.

Track the run via `asl worker-tasks-list` (see `ai-shipping-labs-prod-api`) and re-`GET` the series — `zoom_meetings_last_run` holds `created` / `skipped_ineligible` counts.

Preview, then create:

```bash
uv run asl event-series-zoom-meetings "$SERIES_ID" --dry-run
uv run asl event-series-zoom-meetings "$SERIES_ID"
```

## Banner image

Every event has one 1200x630 image that serves as both the on-page banner and the social `og:image` / `twitter:image`. The API renders it through the banner-generator pipeline as a background task.

Auto-generation on create. `asl events-create` auto-generates the banner by default — `generate_banner` is treated as `true` when omitted. Pass `"generate_banner": false` to opt out. The render runs async, so `banner_url` is empty in the create response and fills in once the worker finishes; the response carries a `banner_task_id` to poll.

Regenerate an existing event. `asl events-regenerate-banner <slug>` force-enqueues a fresh render and returns the task id. This is allowed for synced/GitHub-origin events too.

```bash
uv run asl events-regenerate-banner "$SLUG"
```

Async poll. The render is a background task, so poll `asl worker-task-get <task_id>` (using the `banner_task_id` from create or the `task_id` from regenerate) or just re-run `asl events-get <slug>` and read `banner_url` once it is non-empty.

## Workshops

Workshops are NOT created through the events API. They are git content:

- Workshop markdown lives in the `AI-Shipping-Labs/workshops-content` repo and is synced into the Django DB by the content-sync pipeline (parse markdown/YAML, upload images to S3, upsert).
- Each workshop needs the required frontmatter, including a stable `content_id` — the URL must be derivable from content, never a server-assigned auto-id (the opposite of Studio events).
- To add or update a workshop: edit the markdown in `workshops-content` (frontmatter + body), commit/push, then trigger a content sync so prod picks it up.
- Trigger a sync via `asl sync-source-trigger <uuid>` (or the Studio "Force resync" button at `/studio/sync/`). See `ai-shipping-labs-prod-api` for the sync source list and trigger command, and for watching the resulting background task.

Events vs workshops, crisply:

- Event = Studio/API surface, mutable via create/update, server-assigned ids/slugs are fine.
- Workshop = git content, edited in `workshops-content`, URLs must be content-derivable; the only "publish" action over the API is triggering a sync.

## Verify

After any write, follow the safe-write protocol:

- Event: `asl events-get <slug>` and confirm the fields you set, then check it renders at `https://aishippinglabs.com/events/<id>/<slug>` (published `upcoming` events are publicly visible).
- Series: `asl event-series-get <id>` and confirm cadence/occurrences; check the public series page.
- Zoom: re-run `asl event-series-get <id>` for `zoom_meetings_last_run`, and `asl worker-tasks-list` for the worker outcome.
- Workshop: after `trigger`, watch `asl worker-tasks-list` for the sync task, then load the workshop page.
