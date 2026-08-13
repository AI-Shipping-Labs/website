# Studio UX/UI audit — complexity and design uniformity

Date: 2026-08-13. Auditor: designer agent. Scope: `/studio/` staff surface only.
User complaint, verbatim: "studio is too complicated now. also the design is not uniform."

Authority documents used for judgment: `_docs/design-system.md` (Studio page header,
Studio action/button contracts, casing, date vocabulary, empty states) and
`_docs/studio-conventions.md` (pills, list-page baseline, Operations group).
No new design rules are invented here; anything requiring a new pattern is listed
under open questions.

## 1. Scope and pages audited

`studio/urls.py` registers 313 paths; 119 templates extend `studio/base.html` as full
pages. Screenshotting everything is not useful, so 18 desktop pages (1280x900) and
8 mobile pages (393x851) were captured to cover every distinct page archetype:

| Archetype | Page(s) captured | Why this one |
|---|---|---|
| Dashboard | `/studio/` | The hub; sets expectations for the rest |
| Flagship list with stats + facets | `/studio/users/` | Densest list page; hand-rolled filter chips |
| Baseline model list | `/studio/events/`, `/studio/articles/` | The two reference list implementations (`studio_header_actions` + `studio_list_filter`) |
| List with custom filter panel | `/studio/plans/` (documented exception), `/studio/crm/` (documented exception) | Sanctioned deviations — check they stayed within their sanction |
| Log browser | `/studio/ses-events/` | Chips + secondary filter panel exception |
| Dense entity detail | `/studio/users/5/` | Longest detail page in Studio |
| Operational detail | `/studio/sprints/1/` | Section-heavy detail with actions, danger zone |
| Entity detail with preview | `/studio/campaigns/1/` | Label/value dialect, send controls |
| Synced-content detail | `/studio/workshops/1/` | Origin panel, read-only sync UI |
| Form-heavy create | `/studio/events/new` | Longest create form |
| Long-form editor | `/studio/articles/5/edit` | Sticky save bar reference |
| Settings | `/studio/settings/` | Card-per-group settings dialect |
| Operational queue | `/studio/worker/` | Unbounded operational page |
| Ops dashboard | `/studio/sync/` | Card-per-repo ops dialect |
| One-off tool page | `/studio/tier_overrides/` | Underscore URL; near-empty page |
| List index | `/studio/campaigns/` | Communication-group list |

Mobile (Pixel 7) captures: dashboard, users list, user detail, events create, sprint
detail, settings, CRM, worker.

Capture notes: the dev-only environment-mismatch banner and the optional-analytics
consent modal appear in some captures; both are dev artifacts, not findings. The
consent modal overlaying Studio content on every fresh session is itself noted as
finding B-12.

### Screenshot evidence (CloudFront)

| Page | Desktop 1280x900 | Pixel 7 393x851 |
|---|---|---|
| `/studio/` | https://d31nukezbn4e3o.cloudfront.net/2026/08/13/17ff12d2bcf048c2bd60a1119e7273b9-c31f3d0303173a54.png | https://d31nukezbn4e3o.cloudfront.net/2026/08/13/965f8633340640caaedf544ac03657e7-008359f392c6043a.png |
| `/studio/users/` | https://d31nukezbn4e3o.cloudfront.net/2026/08/13/ea2f741efd99456abf68c740bc003675-714ce8287992fd02.png | https://d31nukezbn4e3o.cloudfront.net/2026/08/13/2c46dcadb9814262ae388d3019c56dcc-63560ca7731507e2.png |
| `/studio/users/5/` | https://d31nukezbn4e3o.cloudfront.net/2026/08/13/d59bdb7bab1c4b88a3799632cde4bc3d-d6124b4bcfab8a80.png | https://d31nukezbn4e3o.cloudfront.net/2026/08/13/710c4bf1fa3b4d63a2ee0e11baee1ee5-3c83611a3aaf3edc.png |
| `/studio/events/` | https://d31nukezbn4e3o.cloudfront.net/2026/08/13/ccb58df189bc48aba02a9f0285d6232e-94afe3d598bbac64.png | — |
| `/studio/events/new` | https://d31nukezbn4e3o.cloudfront.net/2026/08/13/bad67aaacf7e4f7f852313a6e584f767-f3bd7825ba563929.png | https://d31nukezbn4e3o.cloudfront.net/2026/08/13/416252c2a3d742f58bb5f124b59f0120-9cfa6055930cc271.png |
| `/studio/articles/` | https://d31nukezbn4e3o.cloudfront.net/2026/08/13/3c9779ef458c45ec9d816be2717589a2-dd853b99ac875595.png | — |
| `/studio/articles/5/edit` | https://d31nukezbn4e3o.cloudfront.net/2026/08/13/9ddb512e8f534427a41f1a07827e5033-4d55a9d644c43e1d.png | — |
| `/studio/campaigns/` | https://d31nukezbn4e3o.cloudfront.net/2026/08/13/b91c3a403d10449a9c3bd7d343d2a84c-9a60b4c5083e9532.png | — |
| `/studio/campaigns/1/` | https://d31nukezbn4e3o.cloudfront.net/2026/08/13/7b55ed3d5e9544449d17e766de62e445-23f06998859456e0.png | — |
| `/studio/sprints/1/` | https://d31nukezbn4e3o.cloudfront.net/2026/08/13/5ded84e3fd794b998e78e10c3930e210-9402853b1a8ba934.png | https://d31nukezbn4e3o.cloudfront.net/2026/08/13/137140d1510b4cf8b38f9c9d0d545379-578eab0ec069db93.png |
| `/studio/plans/` | https://d31nukezbn4e3o.cloudfront.net/2026/08/13/123a2b47815d4322bd18fa437a638ec7-eca45ad342e854eb.png | — |
| `/studio/crm/` | https://d31nukezbn4e3o.cloudfront.net/2026/08/13/02a9ed84dd1a4608839cf973ced40b7f-7d2e2f1fa1c20928.png | https://d31nukezbn4e3o.cloudfront.net/2026/08/13/f3ca2ff1fde946b2b58bdc36673012e4-12afe52e0d5bacab.png |
| `/studio/settings/` | https://d31nukezbn4e3o.cloudfront.net/2026/08/13/9aa5ba273fc149c88b1fb16929f24165-09cb80bb7d0c3d0f.png | https://d31nukezbn4e3o.cloudfront.net/2026/08/13/88b9b87c0e7f42aaa9cd802bc85a91f3-7a6dd831abb2b11b.png |
| `/studio/worker/` | https://d31nukezbn4e3o.cloudfront.net/2026/08/13/d63c7ed078454971960952737ec08ed2-43b6e25aa31bca0d.png | https://d31nukezbn4e3o.cloudfront.net/2026/08/13/70ab3a574f904ebbb4343228a671b493-765a71968b9ada07.png |
| `/studio/sync/` | https://d31nukezbn4e3o.cloudfront.net/2026/08/13/8375e314472f4fc9ac23a9cf28be6dbf-117cf85269ecef30.png | — |
| `/studio/tier_overrides/` | https://d31nukezbn4e3o.cloudfront.net/2026/08/13/b1e68e3d79784da38366c206e780567b-6bc3e475d327aaf5.png | — |
| `/studio/ses-events/` | https://d31nukezbn4e3o.cloudfront.net/2026/08/13/9f9f3b57aa394ee8a8fd7211ace034e3-d4bbd09b46478480.png | — |
| `/studio/workshops/1/` | https://d31nukezbn4e3o.cloudfront.net/2026/08/13/f5d3c7261205476da9c521dfcbbe0a54-d245d853d7d742fa.png | — |

## 2. Executive summary

Complexity. The sidebar renders roughly 49 navigation entries across 8 collapsible
groups plus a nested Triggers sub-group, and the grouping is by accident of
implementation history rather than by operator task. The People group alone holds
12 entries mixing an entity list (Users), one-shot tools (Merge accounts, New user),
payment operations (Payment mismatches, Subscription reconciliation), a pipeline
(Imports), and two unrelated features (CRM, AI Assistant). Email operations are
split across two groups. At least four working pages are reachable from no
navigation at all. On top of the nav problem, the flagship pages are individually
overloaded: the user detail page stacks 12 full-width cards (a 9,000px scroll on
mobile), and the users list shows six always-expanded filter dimensions plus five
stat cards and a membership matrix before the first table row.

Uniformity. Studio has good shared owners — `{% studio_header_actions %}`,
`{% studio_list_filter %}`, `{% studio_empty_state %}`, `list_pager.html`,
`sticky_action_bar.html` — but adoption stalled at roughly half the surface: 45 of
119 full pages still hand-roll their header, 7 list pages hand-roll search filters,
3 different pager implementations exist, and table cell padding splits three ways
(287 cells `px-4 py-3` per the design system, 187 `px-6 py-4`, 140 `px-6 py-3`).
Nine distinct h1 class strings and nine distinct accent-button padding pairs exist.
Title Case survives in page titles, section titles, and button labels despite the
sentence-case rule. The fix is not a redesign; it is finishing the migration to the
owners the design system already names.

## 3. Axis A — information architecture and complexity

### A-1. The sidebar is a 49-item flat map of the codebase

`templates/studio/base.html` lines 189-737 render: 2 utility rows, Dashboard,
search, then Events (3), Content (7), People (11-12 with superuser), Planning (3),
Onboarding & intake (2), Communication (4), Tracking (3), Operations (10 + 4 nested
Triggers + API tokens + API docs). Each new issue appended one link to whichever
group felt closest; nothing was ever merged or retired. Operators must know the
implementation history to guess that Subscription reconciliation lives under
People while Email log lives under Operations.

### A-2. The People group is a junk drawer

Users, Call profiles, Imports, Instructors, Tier overrides, Tags, Merge accounts,
Payment mismatches, Subscription reconciliation, New user (superuser), CRM,
AI Assistant. Four different domains (membership, payments, data pipelines,
engagement tooling) share one group. Merge accounts and New user are actions on
users, not places — they belong in the Users list header/overflow, not in global
navigation. The AI Assistant operates on CRM records (`studio/urls.py` comment:
"Registered in the People/CRM group") and belongs inside CRM.

### A-3. Email operations are split across two groups

Email campaigns and Email templates sit under Communication; Email log and SES
events sit under Operations; Notifications (the send log for content notify
actions) is under Communication. An operator tracing "did this campaign reach the
member" crosses two sidebar groups and three list dialects. These are one domain:
compose (campaigns, templates), deliver (email log), and deliverability triage
(SES events).

### A-4. Orphaned pages: working tools with no navigation entry

Reachable only by URL or from deep links:

| Route | What it is |
|---|---|
| `/studio/payments/stripe-webhooks/` | Stripe webhook cancellation diagnostics (#1314) — in no sidebar group |
| `/studio/events/duplicates/` | Duplicate-event merge tool (#881) — reachable only from the Events overflow menu |
| `/studio/questionnaire-responses/` | Response review queue — the dashboard links it, the sidebar does not |
| `/studio/users/import/` | CSV contact import — reachable only via the Users header `Import contacts` button |

Header/overflow reachability is acceptable for the last three; the Stripe webhooks
dashboard is genuinely unfindable.

### A-5. The same object is reachable through parallel, inconsistent routes

- Tier overrides: a global page `/studio/tier_overrides/` (underscore URL), a
  per-user page `/studio/users/<id>/tier_override/`, and an inline grant card on
  the user detail page. Three surfaces for one small model. The sidebar active-state
  check has to match three spelling variants (`base.html` line 392:
  `'tier_override' in request.path or 'tier_overrides' in request.path or 'tier-override' in request.path`).
- Importing people: `/studio/imports/` (external pipeline batches) and
  `/studio/users/import/` (CSV contacts) are two different "import users" concepts
  with different UIs; nothing on either page mentions the other.
- Host-like registries: Event hosts (`/studio/hosts/`, Events group), Call profiles
  (`/studio/call-hosts/`, People group), Instructors (`/studio/instructors/`,
  People group). Three registries of "people who appear on content" in two groups.
- UTM links (`/studio/utm-campaigns/`) and UTM analytics (`/studio/utm-analytics/`)
  are two sidebar entries over the same campaign objects; the detail pages
  cross-link but list columns and headers differ.

### A-6. URL and route-convention drift feeds the nav confusion

`tier_overrides/` is the only underscore path among 313. Trailing-slash usage is
mixed even within one entity (`events/new` vs `imports/new/` vs `api-tokens/new/`;
`sprints/<id>/edit` vs `plans/<id>/edit/`). The sidebar consequently matches active
state with substring checks (`'articles' in request.path`, `'/crm' in request.path`)
that are fragile and already needed special-casing (`'/campaigns' in request.path and 'utm-campaigns' not in request.path`).

### A-7. Per-page density: the flagship pages need progressive disclosure

- `/studio/users/<id>/` renders 12 stacked full-width cards: Profile, Email aliases,
  Membership & community, Grant temporary upgrade, Deliverability, Email history,
  Tags, Plans & sprints, Event registrations, Course context, Activity, CRM. Every
  card renders expanded for every user, including cards that are almost always empty
  (Tags, Plans & sprints) and staff-rarely-used tools (Grant temporary upgrade,
  Deliverability bounce controls). Mobile scroll is ~9,000px. It needs an on-page
  section index and collapsed-by-default tool sections.
- `/studio/users/` shows 5 stat cards, a membership matrix, and six filter rows
  (tier, Slack, lifecycle, bounce, subscription, tags) before the table. Only tier
  and search are everyday facets; the rest belong behind a `More filters`
  disclosure.
- `/studio/worker/` renders the pending queue (paginated) plus a 150-row recent
  tasks table plus failed tasks on one page — the desktop capture is ~8,800px tall.
  Recent tasks should default to a much smaller cap with a filter, per its own
  documented "capped operational snapshots" intent.
- `/studio/settings/` renders every integration group on one page with chip
  anchors; the chips filter but all cards still load. Acceptable, but the page
  would read better if the chips were true tabs (one group rendered at a time).

### A-8. Proposed revised IA

Principle: group by operator job, not by app module. Actions on an entity
(merge, import, new) live on that entity's list page, not in the sidebar. Every
page is reachable from exactly one sidebar home. Target: 8 groups, no group over 8
items, ~35 sidebar entries total (from ~49).

```
Dashboard
Content
  Articles | Marketing pages | Courses | Workshops | Projects | Recordings | Downloads
  Content sync                (moved from Operations: it is content operations)
Events
  Events                      (Past events, Duplicates as header tabs — unchanged)
  Event series
  Hosts & instructors         (merge Event hosts + Call profiles + Instructors — see open question Q1)
People
  Users                       (absorbs: New user, Merge accounts, Import contacts, Export CSV as header/overflow actions)
  Imports                     (external pipeline batches; link the CSV import from here)
  CRM                         (absorbs AI Assistant as a CRM header action)
  Personas                    (moved from Onboarding & intake)
  Tags
Programs                      (renamed Planning; the operator job is running member programs)
  Sprints | Plans | Book club
  Questionnaires              (moved from Onboarding & intake; response queue as a tab)
Email
  Campaigns | Email templates | Email log | SES events | Notifications | Site banner
Payments                      (new group; currently scattered under People and unlinked)
  Tier overrides | Payment mismatches | Subscription reconciliation | Stripe webhooks
Analytics                     (renamed Tracking)
  UTM                         (links + analytics as tabs of one surface — see open question Q2)
  Signup analytics
System                        (renamed Operations, now honest: infrastructure only)
  Worker | Redirects | Maven events | Triggers (4 nested) | Settings | API tokens | API docs
```

This removes two groups (Onboarding & intake, Communication), retires 6 sidebar
entries into page-level actions, gives payments a findable home, and makes the
group names match what an operator is trying to do.

## 4. Axis B — design uniformity divergences

Reference contracts: design-system.md sections "Studio page header (stacked)",
"Studio overflow menu", "Studio list-page empty states", "Casing", "Date and Time
Vocabulary", plus studio-conventions.md "List Page Baseline".

| # | Archetype | Page / template | What diverges | What the convention should be |
|---|---|---|---|---|
| B-1 | Page header | 45 of 119 full pages, e.g. `templates/studio/worker.html`, `tier_overrides.html`, `email_templates/list.html`, `triggers/*_list.html`, `utm_analytics/*`, `plans/edit.html` | Hand-rolled h1 blocks; 9 distinct h1 class strings (`mt-2`, `mt-3`, `break-all`, `tracking-tight` variants) | Every list/detail/form page renders `{% studio_header_actions %}` (design-system: "Every Studio page—list, detail, and form—uses one stacked header block") |
| B-2 | Casing | `events/form.html` (`New Event`, `Edit Event`), `tier_overrides.html` (`Tier Overrides`, `Search User`), `worker.html` (`Worker Status`), `sync/dashboard.html` (`Content Sync`, `Sync All`, `Last Sync Results`), `campaigns/detail.html` (`Test Send`, `Test Recipients`, `Send Test`, `Eligible Recipients`, `Sent Count`), `events/form.html` sticky bar (`Save Operational Fields`, `Save Changes`) | Title Case in titles, section headings, labels, and button labels | Sentence case everywhere on Studio surfaces (design-system Casing section) |
| B-3 | Table cells | 187 cells `px-6 py-4` and 140 `px-6 py-3` across `users/list.html`, `plans/list.html`, `crm/list.html`, older tables | Three cell-padding dialects | `px-4 py-3` (design-system: "Studio table cells: px-4 py-3") |
| B-4 | List filters | `users/list.html`, `crm/list.html`, `plans/list.html`, `email_log/list.html`, `ses_events/list.html`, `worker.html`, `questionnaires/response_queue.html` | 7 hand-rolled filter/search rows; `users/list.html` alone uses two chip sizes (`px-4 py-1.5 text-sm` and `px-3 py-1 text-xs`); `plans/list.html` uses an accent-filled `Filter` submit while `events/`, `articles/` use a bordered `Search` | `{% studio_list_filter %}` for the primary search/status row (studio-conventions List Page Baseline); CRM, plans, SES keep their documented extra facets but restyle controls to the shared chrome; filter submit buttons are secondary, not accent-filled |
| B-5 | Pagination | `includes/list_pager.html` (27 users) vs `ses_events/_pager.html` vs `events/_past_pager.html` | Three pager implementations with different labels and spacing | One shared pager: `includes/list_pager.html` |
| B-6 | Form save | `sticky_action_bar.html` (14 pages) vs `includes/forms/action_row.html` (5 pages) vs bare bottom-left buttons (`events/form.html` create branch line 318, `users/create.html` line 64) vs bottom-right per-card `Save {{ group.label }}` (`settings/_integration_card.html` line 153) | Four save-control dialects; the same template (`events/form.html`) uses sticky bar for edit and a bare button for create | Form-edit and form-create pages use the sticky Save/Cancel bar (design-system: "Form-edit pages keep the sticky Save/Cancel bar"); settings cards are a per-group exception to document or migrate |
| B-7 | Detail label/value | `sprints/detail.html` (`<dt class="text-xs text-muted-foreground uppercase">`) vs `campaigns/detail.html` (`<p class="text-sm text-muted-foreground">` Title Case) vs `users/detail` plain `text-sm` rows vs `workshops/detail` mixed | No shared fact-row recipe; three label styles across four detail pages | Standardize on the uppercase `text-xs text-muted-foreground` dt recipe — it matches the mobile responsive-table data-labels already rendered by `studio/base.html` (uppercase 0.6875rem muted). Needs a one-line addition to studio-conventions (open question Q3) |
| B-8 | Buttons | Accent-filled buttons appear with 9 padding pairs (95x `px-4 py-2`, 20x `px-6 py-2`, 8x `px-3 py-2`, 7x `px-3 py-1.5`, 7x `px-3 py-1`, plus strays) and 3 radii (`rounded-lg` 129, `rounded-full` 12, `rounded-md` 4) | No single Studio button unit of reuse; each page hand-rolls | Header actions use the documented primary/secondary strings; row actions use `{% studio_list_action %}` / `{% studio_action_class %}`; strays migrate on touch |
| B-9 | Destructive placement | `sprints/detail.html` renders a `Danger zone` section; `events/form.html` puts delete inside the overflow menu; `campaigns/detail.html` styles the non-destructive `Send to N recipients` with red outline chrome | Destructive affordances differ per page, and red is used for a primary non-destructive action (Send) | Destructive actions live last in the overflow menu with the red item recipe (design-system Studio overflow menu). Send should be primary accent with a confirm step, not red (red = destructive semantics) |
| B-10 | Empty columns / icon columns | `events/_list_table.html` Series/Kind/Platform columns render icon-or-dash with no text | Icon-only cells with em-dash noise vs studio-conventions "Empty diagnostic columns should be populated with useful values ... or removed" | Give icons visible text or tooltips, or collapse the three columns into one labeled column |
| B-11 | Date cells | Mixed `2026-07-22` operator dates (correct) alongside wrapped two-line dates in narrow columns (`plans/list.html` Shared column `2026-\n08-05`) | Missing `whitespace-nowrap` on date cells | studio-conventions baseline: operator vocabulary plus `whitespace-nowrap` |
| B-12 | Overlay chrome | All pages: the optional-analytics consent card renders over Studio content mid-viewport | Member-surface consent UI interrupts staff workflows on every fresh session | Suppress or dock the consent banner on `/studio/` paths (staff-only surface) — needs PM confirmation (open question Q4) |

## 5. Recommended class diffs (highest value)

### 5.1 Users list filter chips → one canonical chip recipe

`templates/studio/users/list.html` lines 104 and 134 define two different chip
sizes on the same page. Align both `{% with %}` blocks to one string (the larger,
44px-friendly one), and drop the accent-filled `Filter`-dialect drift elsewhere:

```diff
- {% with chip_base="px-3 py-1 rounded-full text-xs border transition-colors" chip_active="bg-accent text-accent-foreground border-accent" chip_idle="bg-secondary text-muted-foreground border-border hover:text-foreground hover:bg-muted" %}
+ {% with chip_base="px-4 py-1.5 rounded-full text-sm border transition-colors" chip_active="bg-accent text-accent-foreground border-accent" chip_idle="bg-secondary text-muted-foreground border-border hover:text-foreground hover:bg-muted" %}
```

Reasoning: one page must not mix two pill dialects (design-system Pills section);
the `text-sm` variant is the one that meets the tap-target tie-breaker for
page-level controls.

### 5.2 Plans filter submit: accent-filled → secondary

`templates/studio/plans/list.html` line 47:

```diff
- <button type="submit" class="bg-accent text-accent-foreground px-3 py-2 rounded-lg text-sm font-medium hover:opacity-90 transition-opacity">
-   Filter
- </button>
+ <button type="submit" class="bg-secondary border border-border text-foreground px-4 py-2 rounded-lg text-sm font-medium hover:bg-muted transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-background">
-
+   Search
+ </button>
```

Reasoning: accent-filled is reserved for at most one state-changing action per
surface (studio-conventions List Page Baseline); the reference lists (`events/`,
`articles/`) already use a bordered `Search` control, and `New plan` is the page's
one primary.

### 5.3 Table cell padding normalization

Applies to `users/list.html`, `plans/list.html`, `crm/list.html`, and any table
touched during this cleanup:

```diff
- <th class="text-left px-6 py-4 font-medium">Member</th>
+ <th class="text-left px-4 py-3 font-medium">Member</th>
...
- <td class="px-6 py-4">
+ <td class="px-4 py-3">
```

Reasoning: design-system Spacing: "Studio table cells: px-4 py-3". This alone
removes the visibly different table densities between Articles (correct) and
Users/Plans (inflated).

### 5.4 Sentence-case titles and labels

`templates/studio/events/form.html` (and the `studio_title` block):

```diff
- {% studio_header_actions title='New Event' back_url='/studio/events/' back_label='Back to events' %}
+ {% studio_header_actions title='New event' back_url='/studio/events/' back_label='Back to events' %}
```

`templates/studio/campaigns/detail.html` line 43:

```diff
- <p class="text-sm text-muted-foreground">Eligible Recipients</p>
+ <p class="text-sm text-muted-foreground">Eligible recipients</p>
```

Same treatment for `Tier Overrides`, `Search User`, `Worker Status`,
`Content Sync`, `Sync All`, `Last Sync Results`, `Test Send`, `Send Test`,
`Save Operational Fields` → `Save operational fields`, etc.

### 5.5 Hand-rolled page headers → shared header tag

Example, `templates/studio/worker.html` line 9:

```diff
- <div class="mb-8">
-   <h1 class="text-2xl font-semibold text-foreground">Worker Status</h1>
-   <p class="text-sm text-muted-foreground">django-q2 task worker health and recent activity</p>
- </div>
+ {% load studio_filters %}
+ {% studio_header_actions title='Worker' subtitle='django-q2 task worker health and recent activity' %}{% endstudio_header_actions %}
```

Reasoning: design-system: "New headers render through the shared
`{% studio_header_actions %}` block tag". This kills the nine-way h1 drift at the
source.

## 6. Prioritized backlog

Ordered by user-visible impact per unit of work. Each item is independently
actionable and sized S/M/L.

1. Regroup the sidebar per the IA tree in section A-8 (template-only change to
   `templates/studio/base.html` + `studio_sidebar_state`): new groups Email,
   Payments, Programs, System; move Content sync, Questionnaires, Personas; retire
   New user / Merge accounts / AI Assistant entries into page-level actions. (M)
2. Add the missing homes: Stripe webhooks under Payments; questionnaire response
   queue as a tab on Questionnaires; cross-link the two import surfaces. (S)
3. Normalize Studio table cell padding to `px-4 py-3` across `users`, `plans`,
   `crm`, `email_log`, `ses_events` list templates (diff 5.3). (S)
4. Sentence-case sweep across Studio titles, section headings, and button labels
   (diff 5.4); extend the existing casing conventions test to `templates/studio/`.
   (S)
5. Migrate the 7 hand-rolled list filter rows to `{% studio_list_filter %}` chrome,
   including the plans `Filter` button demotion (diffs 5.1, 5.2); keep the
   documented CRM/plans/SES facet exceptions but restyle their controls. (M)
6. Adopt `{% studio_header_actions %}` on the 45 hold-out pages, starting with the
   14 highest-traffic (worker, tier overrides, email templates, triggers lists,
   UTM analytics, campaigns form, dashboard) (diff 5.5). (M)
7. User detail progressive disclosure: add an on-page section index under the
   header; collapse Grant temporary upgrade, Deliverability, Tags, and CRM into
   `<details>` sections closed by default; keep Profile, Membership, Activity open.
   (M)
8. Users list: keep search + tier row visible; move Slack, lifecycle, bounce,
   subscription, and tag facets behind one `More filters` disclosure; collapse the
   membership matrix into a toggle. (M)
9. Consolidate pagination on `includes/list_pager.html`; delete
   `ses_events/_pager.html` and `events/_past_pager.html`. (S)
10. Unify form save chrome: create branches of `events/form.html` and
    `users/create.html` adopt `sticky_action_bar.html`; retire
    `includes/forms/action_row.html` in favor of the sticky bar. (M)
11. Merge UTM links + UTM analytics into one `UTM` surface with `Links` and
    `Analytics` tabs (one sidebar entry). (M)
12. Merge the global tier-overrides page into Users: the standalone
    `/studio/tier_overrides/` search duplicates the user detail grant card; replace
    with an `Overrides` filter/tab on the Users list and redirect the old URL
    (also fixes the underscore URL). (M)
13. Worker page: cap Recent tasks to 25 with the shared pager, and collapse the
    Failed section when empty. (S)
14. Consolidate Event hosts / Call profiles / Instructors into one directory
    surface (pending Q1). (L)
15. Route-convention cleanup: trailing-slash policy and hyphenation documented in
    `_docs/studio-conventions.md`; replace sidebar substring active-matching with
    named-URL prefix matching in `studio_sidebar_state`. (M)

## 7. Open questions for the PM

- Q1. Hosts consolidation: Event hosts, Call profiles, and Instructors have
  different models and sync sources (instructors are content-derived). Is one
  "Hosts & instructors" directory acceptable, or should instructors stay under
  Content? This changes backlog item 14 significantly.
- Q2. UTM: is the links/analytics split intentional (authoring vs reporting
  audiences), or should they merge into one tabbed surface (backlog item 11)?
- Q3. Detail-page fact rows: no Studio label/value recipe is documented. Proposal:
  the `sprints/detail.html` dt recipe (`text-xs text-muted-foreground uppercase`)
  becomes the convention, added to `_docs/studio-conventions.md`. Approve before
  any migration.
- Q4. Should the optional-analytics consent card be suppressed on `/studio/` paths
  entirely, or docked to a corner? Staff consent still matters if staff sessions
  are tracked.
- Q5. Dashboard as router: the dashboard's Attention queue already surfaces
  cross-domain work items. If the PM wants deeper nav cuts (e.g. dropping the
  Tracking group into a dashboard panel), that is a product decision beyond this
  audit's proposal.
- Q6. `Book club` placement: proposed under Programs alongside Sprints, but it is
  also content-adjacent (chapters sync from notes). Confirm the Programs home.

## 8. Remediation strategy

Section 6 lists what to change. This section is about the order to change it in, and
about the one change that determines whether any of it stays fixed.

### 8.1 Root cause: the primitives exist, the enforcement is opt-in

Every divergence in section 4 has a shared owner that already exists and already works:

| Primitive | Defined at | Templates using it |
|---|---|---|
| `studio_header_actions` | `studio/templatetags/studio_filters.py:331` | 73 |
| `studio_list_filter` | `studio/templatetags/studio_filters.py:431` | 15 |
| `studio_list_action` | `studio/templatetags/studio_filters.py:500` | 21 |
| `studio_action_class` | `studio/templatetags/studio_filters.py:325` | — |

There are 178 templates under `templates/studio/`, of which 119 are full pages. So the
question is not "what should the convention be" — it is documented in
`_docs/design-system.md` and `_docs/studio-conventions.md`, and the code to honour it is
written. The question is why 45 pages ignore it.

The answer is in the guard tests. Studio does have convention lint, and it is well built
— `accounts/tests/test_button_class_lint.py` even self-tests, with
`test_lint_fires_when_a_known_bad_string_is_injected` and
`test_action_palette_lint_rejects_synthetic_violation` proving the lint can fail. But
every guard is scoped to an explicit allowlist rather than to the whole directory:

| Guard | Scoping mechanism | Effective coverage |
|---|---|---|
| `studio/tests/test_studio_header_row_consistency.py` | `HEADER_INVENTORY`, a hardcoded set, pinned by `test_inventory_is_exactly_thirty_one_existing_templates` | 31 of 119 pages |
| `accounts/tests/test_button_class_lint.py` | `SCOPED_TEMPLATES`, a 9-entry tuple | 9 templates, none in Studio |
| `accounts/tests/test_button_class_lint.py` action-palette lint | `test_non_studio_templates_have_no_forbidden_action_palette`, which does `if 'studio' in path.parts: continue` | Studio explicitly excluded |

Two consequences follow, and they explain the whole audit:

A new Studio page is conformant-by-default only if someone remembers to add it to
`HEADER_INVENTORY`. Nobody does, because the page passes CI either way. Note that even
among the 73 templates that do use the shared header, only 31 are guarded — so 42
adopters could drift back tomorrow and no test would notice.

And the button lint, the one guard that would have caught the 9-padding-pair spread in
finding B-8, skips Studio by an explicit `continue`. That line is why Studio has 9 accent
button dialects while the member surfaces it does cover have one.

This is the same shape as the file-accretion problem in the test suite: each per-issue
decision is locally correct, nothing applies pressure at the directory level, and the
drift is invisible from inside any single issue.

### 8.2 The inversion

Before any cosmetic sweep, flip the guards from allowlist to denylist. Concretely, for
each of the three guards above:

- Scan every template under `templates/studio/` by `rglob`, not a pinned set.
- Replace the inventory with `KNOWN_EXCEPTIONS`, seeded with exactly the pages that fail
  today, so the suite is green on day one.
- Assert the exception set only shrinks: keep a count assertion like the existing
  `test_inventory_is_exactly_thirty_one_existing_templates`, but pointed at the exception
  list rather than the compliant list, so adding a non-conformant page fails CI and
  removing one requires deliberately editing the number down.
- Delete the `if 'studio' in path.parts: continue` in
  `test_non_studio_templates_have_no_forbidden_action_palette` and seed its exceptions
  the same way.

That last bullet inverts the meaning of the existing count test, which is the whole
point: today the number 31 ratchets nothing, because it counts what already complies.
Pointed at the exceptions, the same assertion becomes a ratchet that can only tighten.

Do this first. Items 3, 4, 5, 6 and 9 in section 6 are large mechanical sweeps across
dozens of templates; running them before the ratchet exists means the drift returns
issue by issue and this audit gets rewritten in three months. Running them after means
each sweep permanently retires a slice of the exception list, and the list becomes the
live progress tracker for the whole cleanup.

### 8.3 Sequencing

| Phase | Items from section 6 | Why here | Blocked by |
|---|---|---|---|
| 0. Ratchet | 8.2 above, plus item 4's casing test extension | Makes every later phase permanent | nothing |
| 1. Mechanical sweeps | 3 (table padding), 4 (sentence case), 9 (pager), 6 (shared header on 45 pages) | Pure template edits, no product decisions, immediately visible, each retires exceptions | Phase 0 |
| 2. Navigation | 1 (sidebar regroup), 2 (missing homes), 15 (named-URL active matching) | The actual fix for "too complicated"; template-only but needs the IA agreed | IA sign-off |
| 3. Density | 7 (user detail disclosure), 8 (users list filters), 13 (worker cap) | Highest per-page relief, but each needs a judgement call on what stays open by default | Phase 2 |
| 4. Structural merges | 11 (UTM), 12 (tier overrides), 14 (hosts), 5 (filter migration) | Change URLs, so they need redirects and carry real regression risk | Q1, Q2 |
| 5. Deferred | 10 (form save chrome), B-7 fact rows, B-12 consent card | Genuinely ambiguous; not worth blocking the rest | Q3, Q4 |

Phases 0 and 1 are the recommended first shipment. They are mechanical, carry no product
risk, need no PM answers, and between them close findings B-1 through B-5, B-8 and B-11 —
that is most of the "design is not uniform" complaint, without touching a single URL.

Phase 2 is what actually answers "too complicated", and it is worth noting it is a
template-only change: the sidebar regroup in item 1 touches `templates/studio/base.html`
and `studio_sidebar_state`, not any view or model. The 49-to-35 entry reduction is
reversible and cheap to try.

### 8.4 Risks to control

Item 15 matters more than its position suggests. The sidebar currently does active-state
matching by URL substring, which is why the lone underscore route `tier_overrides/`
forces triple-variant matching. Any regroup done before that is fixed will silently
produce wrong highlight states. Move item 15 into phase 2 alongside the regroup rather
than leaving it last.

Every URL change in phase 4 needs a redirect, and `_docs/expand-contract-releases.md`
already describes the pattern to use. `tier_overrides` is the one with an existing
inbound link surface, so it needs the redirect regardless of whether item 12 lands.

Do not let the six open questions in section 7 block phases 0 through 3. Q1, Q2 and Q6
gate phase 4 only; Q3 and Q4 gate phase 5 only. Nothing in section 7 blocks the first
shipment.

## Out of scope

- Backend/API changes; this audit proposes template, URL-alias, and navigation work
  only. Redirects are needed wherever URLs change (`tier_overrides`).
- The dev-only environment mismatch banner.
- Member-facing plan/sprint surfaces reached from Studio (`plan_view_as_member`).
- The Django admin links embedded in Studio pages.
