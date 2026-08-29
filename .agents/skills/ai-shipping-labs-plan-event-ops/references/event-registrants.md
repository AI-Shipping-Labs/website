# Event Registrant Lookup Reference

## Current Surfaces

Production event details are available through the authenticated JSON API at `https://aishippinglabs.com/api/events` and `https://aishippinglabs.com/api/events/<slug>`. Use the live OpenAPI spec to confirm exact paths.

As of this skill's creation, the codebase has a Studio CSV export for rosters:

- Route: `/studio/events/<event_id>/registrations.csv`
- URL name: `studio_event_registrations_csv`
- Access: staff web session via `@staff_required`; no token-authenticated API mechanism
- Columns: `email`, `name`, `registered_at`, `tier`, `joined_at`
- Ordering: newest registration first

The CSV route is defined in `studio/urls.py` and implemented by `studio.views.events.event_registrations_csv`.

## Workflow

1. Resolve the event using the production API and record both slug and numeric ID if available.
2. Check the live OpenAPI spec for any current registration-list endpoint. Prefer a token-authenticated API endpoint if one has been added.
3. If no API endpoint exists, use the Studio CSV export only when a valid staff web session is available.
4. Store downloaded rosters under `.tmp/`, not outside the repo.
5. Summarize registrants minimally: counts, emails, names, tier, and joined status only when needed for the user request.

## Do Not Do

- Do not query local SQLite or a Django shell as a substitute for production registrations.
- Do not infer registrants from email logs, Slack messages, Zoom attendance, or calendar events unless the user explicitly asks for a best-effort cross-check.
- Do not paste large rosters into chat unless the user asked for the full list.
