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

All API calls use the `asl` CLI, which resolves the token from `.env` automatically. See `ai-shipping-labs-prod-api` for full auth and the safe-write protocol (GET-before, write, GET-after).

## Discovering commands

```bash
uv run asl events --help               # subcommands: list, get, create, update, regenerate-banner, notify-workshop-ready
uv run asl events create --help         # all create flags
uv run asl event-series --help          # series subcommands
```

## Events (`asl events --help`)

- `asl events list [--status draft|upcoming|completed|cancelled]`
- `asl events get <slug>`
- `asl events create --title "..." --start-datetime "..." [flags]`
- `asl events update <slug> [flags]`

GitHub-origin / synced events are inspectable but read-only (`editable: false`); only Studio/API-origin events can be created and patched.

### Key create/update flags

Run `uv run asl events create --help` for the full list. The important ones:

Required on create: `--title`, `--start-datetime`.

- `--kind standard|workshop|meetup|q_and_a`
- `--platform zoom|custom`
- `--status draft|upcoming|completed|cancelled`
- `--published` / `--no-published`
- `--timezone Europe/Berlin`
- `--required-level open|registered|basic|main|premium` (tier names, not integers)
- `--host-email host@example.com` (auto-registers that platform user as an attendee)
- `--host-ids 1,2` (comma-separated host profile ids)
- `--tags sprint:may-2026,workshop` (comma-separated)
- `--create-zoom` (provisions a real Zoom meeting; idempotent)
- `--generate-banner` / `--no-generate-banner` (auto-generates 1200x630 banner; defaults true on create)

### Quick example

```bash
uv run asl events create \
  --title "Office Hours: May 5" \
  --start-datetime "2026-05-05T17:00:00+02:00" \
  --end-datetime "2026-05-05T18:00:00+02:00" \
  --timezone Europe/Berlin \
  --status upcoming \
  --published \
  --required-level open \
  --create-zoom
```

`draft` and `cancelled` are hidden from public visitors; `upcoming` and `completed` are public. To make an event visible, set `--status upcoming --published`.

The create call never rolls back on a Zoom problem: if Zoom is unconfigured or fails, the event is still created and the response carries a `zoom_error` string instead of a join URL. Retry later with `asl events update <slug> --create-zoom`.

### Host auto-registration

Creating (or publishing) a non-draft upcoming event auto-registers the platform user resolved from `--host-email` as a normal event attendee. They receive the standard registration emails, .ics, and reminders. `--host-ids` is display-only (public host pills) and does not trigger emails.

## Event series (`asl event-series --help`)

A series is the recurring template; occurrences are individual `Event` rows linked to it.

- `asl event-series list` / `get <id>`
- `asl event-series create --name "..." --day-of-week 1 --start-time 17:00 [flags]`
- `asl event-series update <id> [flags]`
- `asl event-series occurrences-bulk <id> --data '{"occurrences":[...]}'` — ADD occurrences (additive)
- `asl event-series occurrences-reconcile <id> --data '{"occurrences":[...]}'` — EXACT-SET (creates missing, cancels extras)
- `asl event-series zoom-meetings <id> [--dry-run]` — provision Zoom meetings for all eligible occurrences

Required on create: `--name`, `--day-of-week` (0=Mon..6=Sun), `--start-time` (HH:MM).

## Banner image

`asl events regenerate-banner <slug>` force-enqueues a fresh 1200x630 banner render. Allowed for synced events too. The render is async — poll with `asl worker tasks get <task_id>` or re-run `asl events get <slug>` and check `banner_url`.

## Workshops

Workshops are NOT created through the events API. They are git content in `AI-Shipping-Labs/workshops-content`. Edit the markdown, commit/push, then trigger a sync via `asl sync trigger <uuid>`. See `ai-shipping-labs-prod-api` for the sync flow.

Events vs workshops:

- Event = Studio/API surface, mutable via create/update, server-assigned ids/slugs are fine.
- Workshop = git content, URLs must be content-derivable; the only "publish" action is triggering a sync.

## Verify

After any write, follow the safe-write protocol:

- Event: `asl events get <slug>` and confirm fields, then check `https://aishippinglabs.com/events/<id>/<slug>`.
- Series: `asl event-series get <id>` and confirm cadence/occurrences.
- Zoom: re-run `asl event-series get <id>` for `zoom_meetings_last_run`, and `asl worker tasks list` for the worker outcome.
- Workshop: watch `asl worker tasks list` for the sync task, then load the workshop page.
