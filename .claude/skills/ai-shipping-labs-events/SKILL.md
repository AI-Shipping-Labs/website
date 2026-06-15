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

All API calls use the shared production token. See `ai-shipping-labs-prod-api` for the full auth, base URL, and safe-write protocol. Read the token inline without echoing it:

```bash
cd /home/alexey/git/ai-shipping-labs
TOKEN=$(grep -E '^API_SHIPPING_LABS_API_TOKEN=' .env | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'" | tr -d '\r')
curl -s -H "Authorization: Token $TOKEN" https://aishippinglabs.com/api/events | python3 -m json.tool | head
```

Base URL `https://aishippinglabs.com/api`, header `Authorization: Token <key>`, no trailing slash. GET-before, write, GET-after.

## Events

- `GET /api/events` — list. Supports a `status` filter (`draft` / `upcoming` / `completed` / `cancelled`).
- `GET /api/events/<slug>` — detail.
- `POST /api/events` — create a Studio-origin event.
- `PATCH /api/events/<slug>` — update.

GitHub-origin / synced events are inspectable but read-only here (`editable: false` in the serialized object); only Studio/API-origin events can be created and patched. Studio source means server-assigned ids/slugs are fine — you do not need a stable content-derived URL the way git content does.

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
| `max_participants` | integer or null | Capacity cap; null means unlimited. |
| `status` | string | `draft` / `upcoming` / `completed` / `cancelled`. |
| `published` | boolean | Publish flag. Setting it true stamps `published_at`. |
| `external_host` | string | Partner host pill: `''` (community), `Maven`, `Luma`, `DataTalksClub`. |
| `host_email` | string | Optional. Email that receives the host calendar invite with host-only management links. Blank falls back to the operator default mailbox (`EVENTS_HOST_INVITE_EMAIL`); when both are unset no invite is sent. |
| `tags` | array of strings | Free-form, e.g. `["sprint:may-2026"]`. |
| `create_zoom` | boolean | Write-only. When `true` and `platform` is `zoom`, provisions a real Zoom meeting and populates `zoom_join_url` / `zoom_meeting_id`. Idempotent (no-op if a meeting already exists). Not returned in responses. |

`draft` and `cancelled` are hidden from public visitors; `upcoming` and `completed` are public. To make an event visible you generally set `status: upcoming` and `published: true`.

### Host calendar invite

Creating (or publishing) a non-draft event via the API automatically sends the host a Luma-style calendar `.ics` invite with host-only management links. The invite goes to `host_email` if set, otherwise to the operator default mailbox `EVENTS_HOST_INVITE_EMAIL` (a Studio setting). When both are unset, no invite is sent and the save still succeeds. Drafts never send. The invite is sent once per event (EmailLog-guarded), so re-saving or a no-op PATCH never re-sends; a PATCH that flips a draft to `upcoming`, or adds a `host_email` to a not-yet-invited published event, triggers the one-time invite. Invite times render in the host's timezone.

Note: an older example in the code uses `status: scheduled`, but that value is NOT in the model's status choices — use `upcoming`. Verify the live enum from the spec if unsure (`GET /api/openapi.json`, path `/api/events`).

### Create example

```bash
curl -s -X POST -H "Authorization: Token $TOKEN" -H "Content-Type: application/json" \
  https://aishippinglabs.com/api/events \
  -d '{
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
    "tags": ["sprint:may-2026"]
  }'
```

Update via slug, e.g. cancel an event:

```bash
curl -s -X PATCH -H "Authorization: Token $TOKEN" -H "Content-Type: application/json" \
  https://aishippinglabs.com/api/events/office-hours-2026-05-05 \
  -d '{"status": "cancelled"}'
```

Create a Zoom event and provision a real Zoom meeting in one call by adding
`"create_zoom": true` (only valid when `platform` is `zoom`):

```bash
curl -s -X POST -H "Authorization: Token $TOKEN" -H "Content-Type: application/json" \
  https://aishippinglabs.com/api/events \
  -d '{
    "title": "Office Hours: May 5",
    "platform": "zoom",
    "start_datetime": "2026-05-05T17:00:00+02:00",
    "status": "upcoming",
    "published": true,
    "create_zoom": true
  }'
# -> 201 with "zoom_join_url": "https://zoom.us/j/88899900011" populated.
```

The create-event call is the primary action and never rolls back on a Zoom
problem: if Zoom is unconfigured or its API fails, the event is still created
(`201`/`200`) and the response carries a non-fatal `zoom_error` string instead
of a join URL. Because `create_zoom` is idempotent, you can safely retry later
with `PATCH /api/events/<slug> -d '{"create_zoom": true}'` once Zoom is
working — it will not overwrite an existing meeting.

## Event series

A series is the recurring template; its occurrences are individual `Event` rows linked back to it (`event_series` FK, `series_position`). The series carries the cadence; the occurrences carry the actual datetimes.

- `GET /api/event-series` — list. `GET /api/event-series/<id>` — detail.
- `POST /api/event-series` — create.
- `PATCH /api/event-series/<id>` — update.
- `POST /api/event-series/<id>/occurrences/bulk` — ADD missing occurrences (additive; never deletes).
- `PUT /api/event-series/<id>/occurrences` — EXACT-SET: declares the full desired occurrence set in one atomic call (creates missing, reuses existing, cancels extras).

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
# 1. Create the series
SERIES=$(curl -s -X POST -H "Authorization: Token $TOKEN" -H "Content-Type: application/json" \
  https://aishippinglabs.com/api/event-series \
  -d '{"name":"Weekly Office Hours","cadence":"weekly","day_of_week":1,"start_time":"17:00:00","timezone":"Europe/Berlin","required_level":0}')
SERIES_ID=$(echo "$SERIES" | python3 -c "import sys,json;print(json.load(sys.stdin)['id'])")

# 2. Bulk-add occurrences (only start_datetime is required per row; title/slug optional)
curl -s -X POST -H "Authorization: Token $TOKEN" -H "Content-Type: application/json" \
  "https://aishippinglabs.com/api/event-series/$SERIES_ID/occurrences/bulk" \
  -d '{"occurrences":[
        {"start_datetime":"2026-05-05T17:00:00+02:00"},
        {"start_datetime":"2026-05-12T17:00:00+02:00"},
        {"start_datetime":"2026-05-19T17:00:00+02:00"}
      ]}'
```

Cancel one occurrence with a PATCH to that occurrence's event slug (`{"status": "cancelled"}`) — this can send cancellation emails to registrants.

## Zoom meetings

Zoom is the default `platform`. There are two wired auto-creation paths.

Single event — pass `"create_zoom": true` on `POST /api/events` or `PATCH /api/events/<slug>`. When the event's `platform` is `zoom` and it has no existing `zoom_meeting_id`, the API calls the same `create_meeting` service the series path uses and stores `zoom_meeting_id` + `zoom_join_url`. It is idempotent (re-requesting on an event that already has a meeting is a no-op, never an overwrite) and fails soft (a Zoom outage still returns the event with a `zoom_error` key — see the create example above). Passing `create_zoom: true` with `platform: custom` is rejected `422`.

Series (bulk) — the explicit series action remains the path for provisioning every occurrence at once:

- `POST /api/event-series/<id>/zoom-meetings` — create a Zoom meeting for every eligible occurrence in the series. Eligible = future (`is_upcoming`), `platform == "zoom"`, and no existing `zoom_meeting_id`. Past / cancelled / draft / `custom`-platform occurrences are skipped.
- Body `{"dry_run": true}` returns a preview: `{"dry_run": true, "eligible_count": N, ...}` with no Zoom calls or enqueues.
- A real run enqueues a background worker and returns `200` with a `status` of `enqueued` (or `noop` when nothing is eligible) plus a `task_id`.
- It is idempotent per occurrence: occurrences that already carry a `zoom_meeting_id` are skipped (`#859`), so a re-POST after a successful run is a noop. Each `create_meeting` call is wrapped so one Zoom error (e.g. a 429) does not abort the batch; a structured summary is persisted to `series.zoom_meetings_last_run`.

Track the run via `GET /api/worker/tasks` (see `ai-shipping-labs-prod-api`) and re-`GET` the series — `zoom_meetings_last_run` holds `created` / `skipped_ineligible` counts. Each created occurrence's event gets `zoom_meeting_id` + `zoom_join_url` populated.

Preview, then create:

```bash
curl -s -X POST -H "Authorization: Token $TOKEN" -H "Content-Type: application/json" \
  "https://aishippinglabs.com/api/event-series/$SERIES_ID/zoom-meetings" \
  -d '{"dry_run": true}'

curl -s -X POST -H "Authorization: Token $TOKEN" -H "Content-Type: application/json" \
  "https://aishippinglabs.com/api/event-series/$SERIES_ID/zoom-meetings" -d '{}'
```

## Workshops

Workshops are NOT created through the events API. They are git content:

- Workshop markdown lives in the `AI-Shipping-Labs/workshops-content` repo and is synced into the Django DB by the content-sync pipeline (parse markdown/YAML, upload images to S3, upsert).
- Each workshop needs the required frontmatter, including a stable `content_id` — the URL must be derivable from content, never a server-assigned auto-id (the opposite of Studio events).
- To add or update a workshop: edit the markdown in `workshops-content` (frontmatter + body), commit/push, then trigger a content sync so prod picks it up.
- Trigger a sync via `POST /api/sync/sources/<uuid>/trigger` (or the Studio "Force resync" button at `/studio/sync/`). See `ai-shipping-labs-prod-api` for the sync source list and trigger command, and for watching the resulting background task.

Events vs workshops, crisply:

- Event = Studio/API surface, mutable via POST/PATCH, server-assigned ids/slugs are fine.
- Workshop = git content, edited in `workshops-content`, URLs must be content-derivable; the only "publish" action over the API is triggering a sync.

## Verify

After any write, follow the safe-write protocol:

- Event: `GET /api/events/<slug>` and confirm the fields you set, then check it renders at `https://aishippinglabs.com/events/<id>/<slug>` (published `upcoming` events are publicly visible).
- Series: `GET /api/event-series/<id>` and confirm cadence/occurrences; check the public series page.
- Zoom: re-`GET` the series for `zoom_meetings_last_run`, and `GET /api/worker/tasks` for the worker outcome.
- Workshop: after `trigger`, watch `GET /api/worker/tasks` for the sync task, then load the workshop page.
