# 084 - Zoom Integration

**Status:** pending
**Tags:** `events`, `integration`
**GitHub Issue:** [#84](https://github.com/AI-Shipping-Labs/website/issues/84)
**Specs:** 07 (Zoom section)
**Depends on:** [083-events](083-events.md)
**Blocks:** —

## Scope

- When admin creates a live event, auto-create Zoom meeting via Zoom API (POST /users/me/meetings)
- Store zoom_meeting_id and zoom_join_url on event record
- Zoom OAuth app credentials in environment config
- Zoom webhook endpoint `POST /api/webhooks/zoom` for recording.completed
- On recording.completed: match meeting to event, create Recording record (spec 06), link to event, set event status to completed
- Auto-recording enabled (cloud recording)

## Acceptance Criteria

- [ ] When admin creates a live event, system auto-creates Zoom meeting via Zoom API (POST /users/me/meetings)
- [ ] zoom_meeting_id and zoom_join_url stored on Event record
- [ ] Zoom OAuth credentials (ZOOM_CLIENT_ID, ZOOM_CLIENT_SECRET, ZOOM_ACCOUNT_ID) configured via env vars
- [ ] `POST /api/webhooks/zoom` validates Zoom webhook signature; returns 400 on invalid
- [ ] On recording.completed event: matches zoom_meeting_id to Event, creates Recording record, links to event, sets event status to completed
- [ ] `[HUMAN]` Creating a live event in admin and verifying Zoom meeting appears in Zoom dashboard
