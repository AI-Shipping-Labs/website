# Studio UX / Jobs-to-be-Done Audit

Date: 2026-07-09
Scope: operator experience in Studio (`/studio/`) — information architecture, click paths per job, list/form capability consistency, cross-links, and gaps requiring the API or GitHub. Evidence: 47 full-page screenshots of a logged-in staff session on a local server with dev data, plus a code-level trace of `studio/urls.py`, `studio/views/`, `templates/studio/`. Companion visual audit: `2026-07-09-studio-ui-design-audit.md`.

## Operator jobs to be done and their paths

| Job | Path from dashboard | State |
|-----|---------------------|-------|
| Publish/edit content (articles, courses, workshops, recordings, downloads, projects) | Content -> list -> editor; authoring happens in GitHub, Studio toggles status/banner/notify | Works as designed (content is git-sourced); synced items are read-only with "Edit on GitHub" handoff |
| Run events | Events -> New -> form -> Save; Zoom card, registrations CSV on editor | Good; events are Studio-native |
| Manage members / CRM | People -> Users -> search -> detail (override, notes, tags, impersonate, track-in-CRM) | Good detail page; gaps in cross-links and API-only actions below |
| Send campaigns | Communication -> Email campaigns -> New -> edit -> detail -> test-send -> send | Works; recipient visibility is count-only |
| Sprint plans | Planning -> Plans -> New, or Sprints -> detail -> Add member; drag-drop autosave editor | Good editor; bulk import is API-only (agent surface) |
| Configure settings | Operations -> Settings | One page renders every section at once (~16,500px tall) |
| Monitor sync/worker/deliverability | Dashboard attention panel -> Sync / Worker / SES events | Strong; the dashboard is monitor-oriented |

## What works well

- Dashboard attention panel (worker down, failed syncs/imports/tasks, drafts, missing Zoom links) maps directly to "what needs me today" and each item links to its fix surface.
- User detail page is a genuine hub: tier override grant with durations, tags, activity, CRM handoff, impersonate, Django-admin escape hatch.
- Event editor: state sidebar, Zoom meeting card, workshop broadcast, post-event follow-up, timezone resolution preview, save bar available at top of a long form.
- Read-only treatment of GitHub-synced content with provenance panel (source repo/file, sync commit, content ID) makes the git-is-source-of-truth model legible.
- Plan editor autosave with save-status pill and optimistic rollback is the best editing experience in Studio.

## Findings

### P0 — highest operator-time leverage

1. No global search. There is no omnibox anywhere in Studio chrome (`templates/studio/base.html` has no search input); finding a user/event/article means: pick section, open list, use that list's search. Member lookup is the most frequent operator job. A header search across users, content, events, campaigns (or a command palette) would shortcut nearly every job. Typeahead JSON endpoints already exist (`studio/urls.py:689` `studio_user_search`).
2. Dashboard quick actions skew to monitoring; the frequent create/lookup jobs are absent: no New event, New campaign, New plan, or user-search action (`studio/views/dashboard.py:212-220`). Adding create shortcuts and a search field to the dashboard (or the global header) removes 2-3 clicks from the most common flows.
3. No unsaved-changes protection on any full-page POST editor: zero `beforeunload`/dirty-state handling in `templates/studio/` and `static/js/studio/` (except the plan editor's autosave). The event form is very long; a mis-click on a sidebar link silently loses all edits. Add a shared dirty-form guard to the sticky-action-bar pattern.

### P1 — capability inconsistencies that surprise

4. List capability matrix is wildly uneven (from code, `studio/views/*`): pagination exists on only 5 of ~19 lists (Users, CRM, Notifications, SES events, Past events) — all others render unbounded and will degrade as data grows; no list anywhere has bulk actions or column sorting; search is missing on Sprints, Event series, Questionnaires, Personas, Payment mismatches. Baseline: every list gets pagination + search; sorting/bulk where operators actually need it.
5. Four save models coexist: full-page POST + sticky bar (most editors), AJAX autosave + status pill (plan editor only), read-only + GitHub handoff (synced content), Django-admin fallback (fields not surfaced). Operators must remember which model they are in. Direction: keep full-POST for simple forms but adopt the plan editor's save-status affordance for long editors (events, courses), and always show which model applies.
6. Missing cross-links between related entities:
   - User detail does not link to the member's plan or sprint enrollment (only path is user -> CRM record -> plan, and only if tracked in CRM).
   - User detail does not show event registrations (only course enrollments).
   - Event registration rows render registrant emails as plain text — not linked to user detail (`templates/studio/events/form.html:324-395`).
   - Campaign detail shows recipient count only; no way to see who received/bounced, no link to SES events filtered by campaign.
7. Common member-ops actions are API-only with no Studio affordance: mark bounced / reactivate, add/remove email alias, CRM export, tier reconcile, bulk redirects, bulk sprint-plan import, event-series bulk occurrences/reconcile. Bulk/agent operations staying API-only is fine by design, but mark-bounced and alias management belong inline on the user detail page where the bounce state is already displayed read-only (`templates/studio/users/detail.html:470-488`).

### P2 — structure and polish

8. Settings is a single ~16,500px page rendering every integration section at once (`studio/views/settings.py:44-120`). It has section nav, but scanning/searching keys is painful and error-prone. Render one section at a time (tabs or per-section pages) plus a key-name filter box.
9. Nav observations: 8 collapsible groups; People holds 9 items mixing daily surfaces (Users, CRM) with rare wizards (Merge, Imports, Payment mismatches, New user); Operations has grown to 12 items with the Triggers pages. Consider a frequency-based split (daily vs admin). Also, superuser-only items (New user, API tokens) mean the two operators see different navs — worth an explicit decision that this is intended.
10. `_docs/studio-conventions.md` is stale: its canonical Operations group list predates the four Triggers pages now in that group (`templates/studio/base.html:590-621`). Update the doc.
11. Orphan pages reachable only from parent pages (Past events, Event duplicates, course access/enrollments/peer-reviews, tag rename/delete with no list UI) — mostly fine as contextual pages, but tags have mutation endpoints (`studio/urls.py:767-768`) with no management surface at all.
12. Users list renders two prominent action buttons per row including Login as (impersonation) styled as a casual primary-adjacent button on all 50 rows. Impersonation is a sensitive action; it belongs on the user detail page (where it also exists) rather than as a high-frequency-looking list action.

## Related

Visual/design findings (conventions compliance, hierarchy, density, consistency) are in `2026-07-09-studio-ui-design-audit.md`. The public guest-facing audits from the same date: `2026-07-09-guest-ux-conversion-audit.md`, `2026-07-09-guest-ui-design-audit.md`.
