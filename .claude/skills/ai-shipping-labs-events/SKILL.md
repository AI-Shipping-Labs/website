---
name: ai-shipping-labs-events
description: Create and manage AI Shipping Labs events and workshops in production. Use when asked to "create an event", "schedule a workshop", "set up an event series", "add a Zoom event", "make a recurring event", "publish a workshop", cancel/reschedule an occurrence, or bulk-create Zoom meetings for a series. Events are created via the production API; workshops are git content synced from the workshops-content repo.
metadata:
  short-description: Create and manage events, event series, and workshops
---

# AI Shipping Labs Events and Workshops

Two distinct surfaces:

- Events and event series — created and edited through the production HTTP API (Studio-owned).
- Workshops — markdown in `AI-Shipping-Labs/workshops-content`, synced via the content pipeline. NOT created through the events API.

See `ai-shipping-labs-prod-api` for auth and the safe-write protocol.

## Discovering commands

```bash
uv run asl events --help
uv run asl events create --help     # all flags
uv run asl event-series --help
```

## Events (`asl events --help`)

- `asl events list [--status draft|upcoming|completed|cancelled]`
- `asl events get <slug>`
- `asl events create --title "..." --start-datetime "..." [flags]`
- `asl events update <slug> [flags]`

GitHub-origin events are read-only (`editable: false`); only Studio/API-origin events can be created and patched.

### Key create flags

Run `uv run asl events create --help` for the full list. Required: `--title`, `--start-datetime`.

- `--kind standard|workshop|meetup|q_and_a`
- `--platform zoom|custom`
- `--status draft|upcoming|completed|cancelled` (default: `upcoming` for create)
- `--publish` / `--no-publish` (default: published for create)
- `--timezone Europe/Berlin`
- `--required-level open|registered|basic|main|premium`
- `--host-email host@example.com` (auto-registers that user as attendee)
- `--host-ids 1,2` (comma-separated host profile ids)
- `--tags sprint:may-2026,workshop` (comma-separated)
- `--create-zoom` (provisions a real Zoom meeting; idempotent)
- `--generate-banner` / `--no-generate-banner`

### Quick example

```bash
uv run asl events create \
  --title "Office Hours" \
  --start-datetime "2026-05-05T17:00:00+02:00" \
  --timezone Europe/Berlin \
  --required-level open \
  --create-zoom
```

Defaults make the event visible: `status=upcoming`, `published=true`. Pass `--status draft` to keep it hidden.

The create call never rolls back on a Zoom problem: if Zoom fails, the event is still created with a `zoom_error` string. Retry with `asl events update <slug> --create-zoom`.

## Event series (`asl event-series --help`)

- `asl event-series list` / `get <id>`
- `asl event-series create --name "..." --day-of-week 1 --start-time 17:00 [flags]`
- `asl event-series update <id> [flags]`
- `asl event-series add-occurrences <id> --data '{"occurrences":[...]}'` — additive
- `asl event-series set-occurrences <id> --data '{"occurrences":[...]}'` — exact-set
- `asl event-series create-zoom <id> [--dry-run]` — provision Zoom for all eligible occurrences

## Banner image

`asl events regenerate-banner <slug>` force-enqueues a fresh render. Poll with `asl worker task <task_id>` or re-run `asl events get <slug>`.

## Workshops

NOT created through the events API. They are git content in `AI-Shipping-Labs/workshops-content`. Edit markdown, push, then `asl sync trigger <uuid>`.
