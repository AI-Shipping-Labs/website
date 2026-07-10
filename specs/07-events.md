# 07 - Events

## Overview

Scheduled live/community events with a public list, calendar, tier-gated registration, optional Zoom/custom join URLs, and post-event recording surfaces. Events are the home for registration and attendance; workshop-linked recordings hand off to the durable workshop learning artifact.

## Data Model

```
Event:
  id: uuid
  slug: string (unique)
  title: string
  description: text               # markdown
  kind: enum                      # "standard", "workshop", "meetup", "q_and_a"
  platform: enum                  # "zoom", "custom"
  start_datetime: datetime
  end_datetime: datetime | null
  timezone: string                # e.g. "Europe/Berlin"
  zoom_meeting_id: string | null  # populated for Zoom events
  zoom_join_url: string | null    # join URL for Zoom or custom-platform events
  location: string | null         # e.g. "Zoom", "Discord", "GitHub repo"
  tags: string[]
  required_level: int             # 0-3
  max_participants: int | null    # null = unlimited
  status: enum                    # "draft", "upcoming", "completed", "cancelled"
  recording_url: string                 # standalone external recording URL
  recording_embed_url: string           # embeddable provider URL
  recording_s3_url: string              # private S3 object URL
  materials: jsonb                      # slides, repos, docs used
  timestamps: jsonb                     # recording chapters
  created_at: datetime
  updated_at: datetime

EventRegistration:
  event_id: FK -> Event
  user_id: FK -> User
  registered_at: datetime
  UNIQUE(event_id, user_id)
```

## Zoom Integration

When staff creates a Zoom meeting for an event:

1. Backend calls Zoom API `POST /users/me/meetings` with:
   - `topic`: event title
   - `start_time`: `start_datetime` in ISO 8601
   - `duration`: difference between start and end in minutes
   - `timezone`: event timezone
   - `settings.auto_recording`: `"cloud"` (so recording is automatically saved)
2. Store returned `meeting_id` and `join_url` on the event record
3. Registered users see the join URL on the event detail page starting 5 minutes before `start_datetime`

After the event ends:

1. Zoom sends a webhook `recording.completed` to `POST /api/webhooks/zoom`
2. Webhook handler:
   a. Matches the `meeting_id` to the event
   b. Downloads the recording from Zoom, uploads it to the recordings bucket, or stores the provider playback URL
   c. Stores the recording on the event recording fields
   d. Sets `event.status = "completed"`
   e. Admin is notified to add timestamps and materials to the event or linked workshop

## Pages

### `/events` — Live event discovery

- Two sections: "Upcoming" and "Past events" by default
- Upcoming: sorted by `start_datetime` asc. Each card: title, date/time (formatted in user's timezone or Europe/Berlin), required tier badge, spots remaining if `max_participants` is set
- Past events: sorted by `start_datetime` desc. If a recording field is populated, show a "Recording available" indicator.
- Visible to everyone. No gating on the calendar page itself.

### `/events?filter=past` — Past event recordings

- Lists published past events that have a recording field populated.
- Uses precise labels such as "Past event recordings" or "Recordings from past events".
- Workshop-linked events link to `/workshops/{slug}` and their recording CTA links to `/workshops/{slug}/video`.
- Standalone legacy recordings keep the event detail URL.

### `/events/calendar` — Event calendar

- Monthly grid on desktop and agenda view on mobile for scheduled live/community events.
- Links each event to its event detail URL.
- Visible to everyone. No gating on the calendar page itself.

### `/events/{id}/{slug}` — Event detail

- Always visible to everyone. Shows: title, description, date/time, status, location
- If user has access and event is upcoming:
  - "Register" button. Clicking calls `POST /api/events/{slug}/register`. Button changes to "Registered ✓" (with option to unregister).
  - If `max_participants` is set and reached, show "Event is full" instead of Register button.
  - Starting 5 min before event: show the event join link
- If user does not have access: show CTA "Upgrade to {tier_name} to join this event" instead of Register button
- If event is completed and has a linked workshop: show a clear "View workshop writeup" handoff to `/workshops/{slug}`.
- If event is completed, standalone, and has a recording: show the inline recording/player and materials using the existing access gate.

## Requirements

- R-EVT-1: Create `events` and `event_registrations` tables with schemas above.
- R-EVT-2: When staff explicitly creates a Zoom meeting for an event, call Zoom API to create a meeting. Store `zoom_meeting_id` and `zoom_join_url` on the event. Requires Zoom OAuth app credentials in environment config.
- R-EVT-3: Implement `POST /api/webhooks/zoom` to handle `recording.completed`. Store recording playback data on the matched event, preserve workshop-linked handoff behavior, and set event status to completed.
- R-EVT-4: `GET /api/events` returns all non-draft events. Accepts `?status=upcoming` or `?status=past` filter. Each event includes `is_locked`, `is_registered` (for authenticated users), `spots_remaining` (if `max_participants` set).
- R-EVT-5: `POST /api/events/{slug}/register` registers the authenticated user if they have access and spots are available. Returns 403 if tier too low, 409 if already registered, 410 if event is full.
- R-EVT-6: `DELETE /api/events/{slug}/register` unregisters the user.
- R-EVT-7: `GET /api/events/{slug}` returns event detail. If user is registered and event starts within 5 minutes, include `zoom_join_url`. Otherwise omit it.
- R-EVT-8: Admin CRUD: `POST /api/admin/events`, `PUT /api/admin/events/{id}`, `DELETE /api/admin/events/{id}`. Status transitions: draft → upcoming → completed. Admin can also manually set to cancelled.
