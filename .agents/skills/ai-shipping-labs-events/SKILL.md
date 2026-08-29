---
name: ai-shipping-labs-events
description: Create and manage AI Shipping Labs events and workshops in production. Use when asked to "create an event", "schedule a workshop", "set up an event series", "add a Zoom event", "make a recurring event", "publish a workshop", invite an event guest, assign an event host, cancel/reschedule an occurrence, or bulk-create Zoom meetings for a series. Events are created via the production API; workshops are git content synced from the workshops-content repo.
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
- `asl events sync-zoom <slug>` — idempotently PATCH the existing Zoom
  meeting from the currently stored event state; it does not edit the event or
  notify attendees

GitHub-origin events are read-only (`editable: false`); only Studio/API-origin events can be created and patched.

### Key create flags

Run `uv run asl events create --help` for the full list. Production currently
requires `--title`, `--description`, and `--start-datetime`.

- `--kind standard|workshop|meetup|q_and_a`
- `--platform zoom|custom`
- `--status draft|upcoming|completed|cancelled` (default: `upcoming` for create)
- `--publish` / `--no-publish` (default: published for create)
- `--timezone Europe/Berlin`
- `--required-level open|registered|basic|main|premium`
- `--host-email host@example.com` (operational host account; auto-registers
  that platform user as an attendee and sends host access/calendar email)
- `--host-ids 1,2` (visible host profile cards; this is independent of
  `host_email`)
- `--tags sprint:may-2026,workshop` (comma-separated)
- `--create-zoom` (provisions a real Zoom meeting; idempotent)
- `--generate-banner` / `--no-generate-banner`

### Quick example

```bash
uv run asl events create \
  --title "Office Hours" \
  --description "Open office hours for questions and project help." \
  --start-datetime "2026-05-05T17:00:00+02:00" \
  --timezone Europe/Berlin \
  --required-level open \
  --host-email alexey@datatalks.club \
  --host-ids 1 \
  --create-zoom
```

Defaults make the event visible: `status=upcoming`, `published=true`. Pass `--status draft` to keep it hidden.

The create call never rolls back on a Zoom problem: if Zoom fails, the event is still created with a `zoom_error` string. Retry with `asl events update <slug> --create-zoom`.

### Alexey host and calendar guest

For events operated by Alexey, always use both host fields:

- `--host-email alexey@datatalks.club` — Alexey's work/platform account and
  operational host identity.
- `--host-ids 1` — Alexey Grigorev's visible event-host profile. Setting only
  `host_email` leaves the serialized `hosts` list empty and no host card is
  shown on the event page.

After every create or host update, follow the safe-write protocol and confirm
that the read-back contains:

```json
{
  "host_email": "alexey@datatalks.club",
  "hosts": [{"id": 1, "slug": "alexey-grigorev"}]
}
```

Also invite `alexey.s.grigoriev@gmail.com` as an ordinary attendee/guest on
every newly created or newly published event, unless Alexey explicitly opts
out. This is separate from host assignment: never replace `host_email` with
the Gmail address and never use Gmail in `host_ids`.

Use the supported `asl events` guest-invitation command once issue #1494 is
implemented. Until that command exists, report that the guest invite is
pending instead of using raw HTTP, bypassing CSRF, or temporarily reassigning
`host_email`.

For a series occurrence, the guest invitation should use whole-series scope so
future eligible occurrences automatically include Gmail. For a standalone
event, use event-only scope. Treat an already-registered response as a
successful idempotent outcome, then GET the event again for the ordinary
safe-write read-back.

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
