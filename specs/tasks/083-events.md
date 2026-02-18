# 083 - Events and Calendar

**Status:** pending
**Tags:** `events`, `admin`, `frontend`
**GitHub Issue:** [#83](https://github.com/AI-Shipping-Labs/website/issues/83)
**Specs:** 07
**Depends on:** [071-access-control](071-access-control.md)
**Blocks:** [084-zoom-integration](084-zoom-integration.md)

## Scope

- Event model: slug, title, description, event_type (live/async), start/end datetime, timezone, zoom_meeting_id, zoom_join_url, location, tags, required_level, max_participants, status (draft/upcoming/live/completed/cancelled), recording_id FK
- EventRegistration model: event FK, user FK, registered_at
- `/events` calendar page: "Upcoming" and "Past" sections, event cards with date/time, type badge, tier badge, spots remaining
- `/events/{slug}` detail page: always visible; Register button if user has access; "Event is full" if capacity reached; Zoom join link 15 min before start; CTA for unauthorized users; link to recording if completed
- Registration/unregistration endpoints
- Admin CRUD with status transitions (draft → upcoming → live → completed, cancelled)

## Acceptance Criteria

- [ ] Event model with fields: slug (unique), title, description (markdown), event_type (live/async), start_datetime, end_datetime, timezone, zoom_meeting_id, zoom_join_url, location, tags (string[]), required_level, max_participants, status (draft/upcoming/live/completed/cancelled), recording_id FK (nullable), created_at
- [ ] EventRegistration model: event FK, user FK, registered_at; unique together (event, user)
- [ ] `GET /events` shows "Upcoming" and "Past" sections; each card: title, date/time, type badge, tier badge, spots remaining
- [ ] `GET /events/{slug}` always visible; shows title, description, date/time, location
- [ ] Authorized user can register; "Event is full" shown when max_participants reached
- [ ] Zoom join link displayed only within 15 minutes before event start_datetime
- [ ] Unauthorized user sees CTA "Upgrade to {tier_name} to attend"
- [ ] Completed event shows link to recording if recording_id is set
- [ ] `POST /api/events/{slug}/register` and `DELETE /api/events/{slug}/register` for registration/unregistration
- [ ] Admin can create/edit/delete events with status transitions: draft → upcoming → live → completed; cancelled from any state
