# 07 - Events

## Overview

Live (Zoom) and async events with a public calendar, tier-gated registration, and automatic post-event recording.

## Data Model

```
Event:
  id: uuid
  slug: string (unique)
  title: string
  description: text               # markdown
  event_type: enum                # "live", "async"
  start_datetime: datetime        # for live: Zoom session start. For async: challenge start date
  end_datetime: datetime | null   # for live: expected end. For async: deadline
  timezone: string                # e.g. "Europe/Berlin"
  zoom_meeting_id: string | null  # populated automatically for live events
  zoom_join_url: string | null    # populated automatically for live events
  location: string | null         # "Zoom" for live, or custom text for async (e.g. "GitHub repo")
  tags: string[]
  required_level: int             # 0-3
  max_participants: int | null    # null = unlimited
  status: enum                    # "draft", "upcoming", "live", "completed", "cancelled"
  recording_id: FK -> Recording | null  # link to recording after event ends
  created_at: datetime
  updated_at: datetime

EventRegistration:
  event_id: FK -> Event
  user_id: FK -> User
  registered_at: datetime
  UNIQUE(event_id, user_id)
```

## Zoom Integration

When admin creates a live event:

1. Backend calls Zoom API `POST /users/me/meetings` with:
   - `topic`: event title
   - `start_time`: `start_datetime` in ISO 8601
   - `duration`: difference between start and end in minutes
   - `timezone`: event timezone
   - `settings.auto_recording`: `"cloud"` (so recording is automatically saved)
2. Store returned `meeting_id` and `join_url` on the event record
3. Registered users see the `zoom_join_url` on the event detail page starting 15 minutes before `start_datetime`

After the event ends:

1. Zoom sends a webhook `recording.completed` to `POST /api/webhooks/zoom`
2. Webhook handler:
   a. Matches the `meeting_id` to the event
   b. Downloads the recording from Zoom (or gets the Zoom cloud URL)
   c. Creates a `Recording` record (see spec 06) with `event_id` set, copies title/description/tags from the event
   d. Sets `event.recording_id` to the new recording
   e. Sets `event.status = "completed"`
   f. Admin is notified to add timestamps and materials to the recording

## Pages

### `/events` — Event calendar

- Two sections: "Upcoming" and "Past"
- Upcoming: sorted by `start_datetime` asc. Each card: title, date/time (formatted in user's timezone or Europe/Berlin), type badge (Live/Async), required tier badge, spots remaining if `max_participants` is set
- Past: sorted by `start_datetime` desc. If `recording_id` is set, show "Watch recording" link
- Optional: calendar view (month grid with event dots). List view is the default and MVP.
- Visible to everyone. No gating on the calendar page itself.

### `/events/{slug}` — Event detail

- Always visible to everyone. Shows: title, description, date/time, type, location
- If user has access and event is upcoming:
  - "Register" button. Clicking calls `POST /api/events/{slug}/register`. Button changes to "Registered ✓" (with option to unregister).
  - If `max_participants` is set and reached, show "Event is full" instead of Register button.
  - Starting 15 min before event: show Zoom join link (for live events)
- If user does not have access: show CTA "Upgrade to {tier_name} to join this event" instead of Register button
- If event is completed and has a recording: show "Watch recording" link to `/recordings/{recording_slug}`

## Requirements

- R-EVT-1: Create `events` and `event_registrations` tables with schemas above.
- R-EVT-2: When admin creates an event with `event_type = "live"`, automatically call Zoom API to create a meeting. Store `zoom_meeting_id` and `zoom_join_url` on the event. Requires Zoom OAuth app credentials in environment config.
- R-EVT-3: Implement `POST /api/webhooks/zoom` to handle `recording.completed`. Create a Recording record, link it to the event, set event status to completed.
- R-EVT-4: `GET /api/events` returns all non-draft events. Accepts `?status=upcoming` or `?status=past` filter. Each event includes `is_locked`, `is_registered` (for authenticated users), `spots_remaining` (if `max_participants` set).
- R-EVT-5: `POST /api/events/{slug}/register` registers the authenticated user if they have access and spots are available. Returns 403 if tier too low, 409 if already registered, 410 if event is full.
- R-EVT-6: `DELETE /api/events/{slug}/register` unregisters the user.
- R-EVT-7: `GET /api/events/{slug}` returns event detail. If user is registered and event starts within 15 minutes, include `zoom_join_url`. Otherwise omit it.
- R-EVT-8: Admin CRUD: `POST /api/admin/events`, `PUT /api/admin/events/{id}`, `DELETE /api/admin/events/{id}`. Status transitions: draft → upcoming → live → completed. Admin can also manually set to cancelled.
