# Studio UI / Design Audit

Date: 2026-07-09
Scope: visual audit of Studio (`/studio/`) against `_docs/studio-conventions.md` (primary) and `_docs/design-system.md` (secondary). Evidence: 47 full-page 1440px light-theme screenshots of a logged-in staff session on local dev, verified against templates. Companion UX audit: `2026-07-09-studio-ux-audit.md`.

## Findings (ordered by severity)

1. Off-palette blue primary button: "Create Zoom Meeting" uses raw `bg-blue-600` (`templates/studio/events/form.html:562`; same blue in `templates/studio/includes/notification_actions.html` and `templates/studio/event_series/detail.html`). Not theme-aware, off the token system, competes with the accent green on the same page. Swap to the canonical accent button classes.
2. Light-mode pill contrast: status/tier pills use `text-green-300`/`text-blue-300`/`text-amber-300` on `bg-*-500/15` per studio-conventions — foregrounds tuned for dark surfaces. In light mode (Users table, User detail, CRM, Hosts, Campaigns, Imports) they are washed out and fail contrast, and they are the primary state signal in dense tables. Fix in `STATUS_BADGE_CLASSES` (`studio/templatetags/studio_filters.py`) and the tier pill helpers with light/dark foreground pairs (e.g. `text-green-700 dark:text-green-300`); update the conventions doc's canonical colors accordingly. Most cross-cutting issue in the set.
3. Payment-mismatches empty state is hand-rolled (`templates/studio/users/payment_mismatches.html:108` — plain sentence in a td), violating the `{% studio_empty_state %}` mandate; should be the `filter` variant with clear-filters affordance.
4. ISO date columns wrap mid-value ("2026-06-\n30") on Articles, Imports, SES events, Worker pending tasks. Add `whitespace-nowrap` to date cells and right-size columns.
5. Worker pending tasks show raw seconds ("expired 1293134s ago" — about 15 days). Humanize the delta in `templates/studio/_worker_pending_tasks.html`.
6. Raw markup leaks into scannable columns: Plans list shows literal `**markdown**` asterisks in titles; Email templates list shows full `{% if %}...{% else %}...{% endif %}` logic in the SUBJECT column. Strip/escape markdown for the plan list cell; truncate/simplify template subjects.
7. Events list uses the member-facing long datetime ("Mon, Jul 13, 2026, 17:00 Europe/Berlin") instead of the operator format mandated by studio-conventions Date/Time vocabulary (`templates/studio/events/_list_table.html:59`).
8. Two competing filled-green primaries in the Events list header ("New event" and "New event series"); conventions specify a single primary CTA. Demote "New event series" to the bordered secondary style.
9. Always-empty columns: Recordings DATE, Event series CADENCE, SES events BOUNCE TYPE and EMAIL LOG all render empty/dashes. Populate (series cadence is known; recordings have dates) or drop.
10. Operations nav overload: 11 items including four developer-named triggers pages ("Event emissions", "Webhook deliveries"). Nest triggers under one sub-group or a tabbed page; update the stale conventions doc list (predates triggers).
11. Sidebar prints literal "vN/A" when VERSION is unset (`templates/studio/base.html:655`). Hide the line when empty.
12. Five different filter-bar layouts across list pages (Articles inline search+dropdown, Projects same minus label, CRM pill tabs + right search, Plans/Imports secondary panel + Filter button, SES/Payment-mismatches tabs + separate search). Pick one canonical treatment; document deliberate exceptions.
13. Primary-action color flips per page: Articles/Recordings/Projects make View the filled green with Edit bordered; Campaigns/Plans make Edit primary. Decide per entity family and apply uniformly.
14. Mixed date vocabularies: Signup analytics uses relative time ("6 days ago") against the operator `Y-m-d` standard; Notifications TARGET URL mixes an absolute localhost URL with relative paths — normalize to relative.
15. Machine-serialized values verbatim: trigger subscription FILTER shows a Python dict repr (`{'name': 'experiment_demo'}`); scheduled-import cards show raw cron (`0 3 * * *`). Render key/value chips and add a human cron gloss ("daily 03:00 UTC").
16. Django admin escape hatches appear twice with different treatments on User detail ("Open in Django admin" button + inline "Edit in Django admin" link). Open product call: if admin stays an intentional escape hatch, make it one consistently-styled secondary action.

Minor: no branded 404 inside Studio (bad URLs render the Django debug page in dev).

## What Studio does well (propagate these)

- Consistent detail-page header pattern (eyebrow + H1 + subtitle left, actions right).
- Sticky Save/Cancel bar on long edit forms.
- Destructive actions correctly use the red-bordered secondary treatment (Redirects delete, API token revoke, Worker delete).
- Secrets masked everywhere (token keys, trigger secrets).
- Pill-vs-plain semantics broadly faithful to conventions (states are pills; emails/IDs/dates plain).
- Stat-card count strips above tables (Users, Worker, SES events, Signup analytics).
- Contextual worker-health warnings on the pages where the worker matters (Campaigns, Notifications) plus the dashboard alert.
- Dashboard Attention panel maps one-to-one to real operator jobs.
