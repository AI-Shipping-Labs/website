# Container width audit — user-facing pages

| Field | Value |
|---|---|
| Audit date | 2026-07-21 |
| Implemented | 2026-07-21 |
| Status | Implemented and enforced by tests |
| Contract | `_docs/design-system.md` → Spacing and Layout |
| Guard | `content/tests/test_container_widths.py` (tagged `core`) |
| Screenshots | `.tmp/designer-width-audit-desktop/` (1440x900, captured 2026-07-21; local `.tmp/` is not committed and will not survive a clean checkout) |

Scope: every user-facing route (marketing, content, events, sprints/plans, voting, accounts, community). Studio and `templates/integrations/` admin surfaces are excluded from the recommendation and noted separately at the end.

The sections below are preserved as the 2026-07-21 audit recorded them, so the reasoning stays auditable. What actually shipped, including four offenders the manual audit missed, is in [Implementation record](#implementation-record) at the end. Where the two disagree, the implementation record is authoritative.

## Current state

Six distinct outer container widths are in use on user-facing pages: `max-w-7xl`, `max-w-5xl`, `max-w-4xl`, `max-w-3xl`, `max-w-2xl`, and `max-w-lg`. The site chrome baseline is `max-w-7xl` (`templates/includes/header.html:9`, `templates/includes/footer.html:8` and `:39`), so any page whose outer frame is narrower than 7xl shows its content column visibly inset from the header logo/nav.

`_docs/design-system.md` (lines 119-127) already prescribes a three-tier system — `max-w-7xl` for marketing/listing, `max-w-5xl` for richer detail, `max-w-3xl` for reader/long-form — but three pages violate it outright and a fourth de-facto tier (`max-w-2xl` narrow status/form pages, 10 templates) exists without being written down.

Worst inconsistencies, confirmed visually:

1. `/sprints` (the reported complaint) is an index page rendered at `max-w-5xl` while every sibling index page — `/events`, `/blog`, `/projects`, `/tutorials`, `/downloads`, `/courses`, `/workshops`, `/resources`, `/interview`, `/tags`, `/vote` — uses `max-w-7xl`. At 1440px the sprint list sits ~200px narrower than the header above it and is left-aligned inside the chrome, so the right side of the page looks empty (see `sprints_1440x900.png` vs `events_1440x900.png`).
2. `/events/<id>/host/manage` uses `max-w-4xl` — the only 4xl page on the site, matching no tier.
3. `/events/<slug>/cancel-registration` confirm and result pages use `max-w-lg` — narrower than every other status page; the sibling join-state pages (`join_countdown`, `join_too_early`, `join_unavailable`) use `max-w-2xl`.
4. Horizontal padding drift: the standard gutter is `px-4 sm:px-6 lg:px-8`, but ~10 pages use `px-6 lg:px-8` (no `px-4` base step), giving them wider mobile gutters than sibling pages. Same-width pages therefore still misalign at small viewports.

## Proposed width system

Four tiers, three of which are already the documented contract in `_docs/design-system.md`. The rule is based on content shape, never per-page taste.

| Tier | Class string | Rule |
|---|---|---|
| Frame | `mx-auto max-w-7xl px-4 sm:px-6 lg:px-8` | Index/grid/listing pages, marketing pages, the member dashboard, and any sidebar-plus-content layout. The outer frame always matches the header/footer chrome; narrower inner columns (intros at `max-w-3xl`, auth card at `max-w-md`) live inside it. |
| Detail | `mx-auto max-w-5xl px-4 sm:px-6 lg:px-8` | Detail pages with mixed layout: media embed plus metadata plus cards/CTAs (event, course, workshop, sprint, plan, poll detail; account; notifications). Wide enough for two-column metadata, narrow enough that single-column sections do not sprawl. |
| Reader | `mx-auto max-w-3xl px-4 sm:px-6 lg:px-8` | Long-form prose (`.prose` bodies: blog, tutorial, project, interview detail, legal, FAQ, about) and multi-step single-column forms (onboarding, sprint feedback). |
| Narrow | `mx-auto max-w-2xl px-4 sm:px-6 lg:px-8` | Terminal status/confirmation interstitials and single-purpose forms: subscribe, join-state pages, cancel registration, account deleted, email-change result, peer-review submit/review/certificate, Slack join denied. |

Reader tier measure: `max-w-3xl` is 48rem (768px); after the `lg:px-8` gutters the text column is 704px. At the `.prose` body size (16px Inter, `line-height: 1.75`) that is roughly 80-90 characters, and about 75 characters at the `text-lg` lead size — at or just above the classic 65-75ch readable band. It is the widest class that stays near that band while giving embedded code blocks and images usable width, which is exactly why reader pages must stay at 3xl rather than being widened toward the chrome; `max-w-5xl` prose would run ~120ch and become unreadable. Anything narrower than 3xl starves code samples. This matches the settled contract in `_docs/design-system.md`.

The Narrow tier is the one addition to the documented system: it already exists in 10 templates as a consistent pattern and should be codified in `_docs/design-system.md` so future status pages stop improvising (`max-w-lg` on the cancel pages is what improvisation produces).

## Full route table

Current and proposed refer to the outermost content container of the page body. Padding-only fixes (adding the `px-4` base step) are marked PAD.

### Marketing and public

| Route | Current | Proposed | Verdict | Template | Reason |
|---|---|---|---|---|---|
| `/` (anonymous) | `max-w-7xl` | `max-w-7xl` | KEEP | `templates/home.html:33` | Marketing page, matches chrome |
| `/` (signed in, dashboard) | `max-w-7xl` | `max-w-7xl` | KEEP | `templates/content/dashboard.html:26` | Dashboard grid, Frame tier |
| `/about` | `max-w-3xl` | `max-w-3xl` | KEEP | `templates/content/about.html:12` | Long-form prose |
| `/activities` | `max-w-7xl` | `max-w-7xl` | PAD | `templates/content/activities.html:16` | Frame correct; `px-6 lg:px-8` missing `px-4` base (later sections at :190/:266/:300 already standard) |
| `/pricing` | `max-w-7xl` | `max-w-7xl` | KEEP | `templates/payments/pricing.html:43` | Marketing/listing |
| `/faq` | `max-w-3xl` | `max-w-3xl` | KEEP | `templates/content/faq.html:11` | Reader tier |
| `/request-a-call` | `max-w-3xl` | `max-w-3xl` | KEEP | `templates/content/request_a_call.html:12` | Single-column form/prose |
| `/terms` | `max-w-3xl` | `max-w-3xl` | KEEP | `templates/legal/terms.html:11` | Long-form prose |
| `/privacy` | `max-w-3xl` | `max-w-3xl` | KEEP | `templates/legal/privacy.html:11` | Long-form prose |
| `/impressum` | `max-w-3xl` | `max-w-3xl` | KEEP | `templates/legal/impressum.html:11` | Long-form prose |
| `/subscribe` | `max-w-2xl` | `max-w-2xl` | PAD | `templates/email_app/subscribe.html:11` | Narrow tier; `px-6 lg:px-8` gutter drift |

### Content

| Route | Current | Proposed | Verdict | Template | Reason |
|---|---|---|---|---|---|
| `/blog` | `max-w-7xl` | `max-w-7xl` | KEEP | `templates/content/blog_list.html:15` | Index/grid |
| `/blog/<slug>` | `max-w-3xl` | `max-w-3xl` | KEEP | `templates/content/blog_detail.html:19` | Reader tier |
| `/tutorials` | `max-w-7xl` | `max-w-7xl` | KEEP | `templates/content/tutorials_list.html:14` | Index/grid |
| `/tutorials/<slug>` | `max-w-3xl` | `max-w-3xl` | KEEP | `templates/content/tutorial_detail.html:15` | Reader tier |
| `/projects` | `max-w-7xl` | `max-w-7xl` | KEEP | `templates/content/projects_list.html:12` | Index/grid |
| `/projects/<slug>` | `max-w-3xl` | `max-w-3xl` | KEEP | `templates/content/project_detail.html:17` | Reader tier |
| `/downloads` | `max-w-7xl` | `max-w-7xl` | KEEP | `templates/content/downloads_list.html:15` | Index/grid |
| `/downloads/<slug>` | `max-w-5xl` | `max-w-5xl` | KEEP | `templates/content/download_detail.html:13` | Detail with inner 3xl content columns |
| `/resources` | `max-w-7xl` | `max-w-7xl` | KEEP | `templates/content/collection_list.html:14` | Index/grid |
| `/resources/<id>/go` interstitial | `max-w-3xl` | `max-w-2xl` | CHANGE | `templates/content/curated_link_verify_required.html:9` | Status interstitial belongs in Narrow tier; also PAD (`px-6 lg:px-8`) |
| `/interview` | `max-w-7xl` | `max-w-7xl` | KEEP | `templates/content/interview_hub.html:10` | Index/grid |
| `/interview/<slug>` | `max-w-3xl` | `max-w-3xl` | KEEP | `templates/content/interview_detail.html:16` | Reader tier |
| `/tags` | `max-w-7xl` | `max-w-7xl` | KEEP | `templates/content/tags_index.html:11` | Index/grid |
| `/tags/<tag>` | `max-w-7xl` | `max-w-7xl` | KEEP | `templates/content/tags_detail.html:12` | Index/grid |
| `/workshops` | `max-w-7xl` | `max-w-7xl` | KEEP | `templates/content/workshops_list.html:10` | Index/grid |
| `/workshops/catalog` | `max-w-7xl` | `max-w-7xl` | KEEP | `templates/content/_workshops_catalog.html:7` | Index/grid |
| `/workshops/<slug>` | `max-w-5xl` | `max-w-5xl` | KEEP | `templates/content/workshop_detail.html:19` | Detail tier |
| `/workshops/<slug>/video` | `max-w-5xl` | `max-w-5xl` | KEEP | `templates/content/workshop_video.html:18` | Detail tier (video embed) |
| `/workshops/<slug>/tutorial/<page>` | `max-w-7xl` | `max-w-7xl` | PAD | `templates/content/workshop_page_detail.html:22` | Sidebar+content layout, Frame tier; `px-6 lg:px-8` drift |
| `/courses` | `max-w-7xl` | `max-w-7xl` | KEEP | `templates/content/courses_list.html:14` | Index/grid |
| `/courses/<slug>` | `max-w-5xl` | `max-w-5xl` | KEEP | `templates/content/course_detail.html:19` | Detail tier |
| `/courses/<c>/<m>` | `max-w-5xl` | `max-w-5xl` | KEEP | `templates/content/module_overview.html:12` | Detail tier |
| `/courses/<c>/<m>/<u>` | `max-w-3xl` / `max-w-7xl` | same | PAD | `templates/content/course_unit_detail.html:24` and `:125` | Reader column and sidebar-layout frame both correct; both use `px-6 lg:px-8` |
| `/courses/<slug>/submit` | `max-w-2xl` | `max-w-2xl` | PAD | `templates/content/peer_review/submit.html:12` | Narrow tier form; `px-6 ... lg:px-8` drift |
| `/courses/<slug>/reviews/<id>` | `max-w-2xl` | `max-w-2xl` | PAD | `templates/content/peer_review/review_form.html:11` | Narrow tier form |
| `/certificates/<id>` | `max-w-2xl` | `max-w-2xl` | PAD | `templates/content/peer_review/certificate.html:11` | Narrow tier |

### Events

| Route | Current | Proposed | Verdict | Template | Reason |
|---|---|---|---|---|---|
| `/events` | `max-w-7xl` | `max-w-7xl` | KEEP | `templates/events/events_list.html:12` | Index/listing, matches chrome |
| `/events/calendar` | `max-w-7xl` | `max-w-7xl` | KEEP | `templates/events/events_calendar.html:11` | Calendar grid needs full frame |
| `/events/<id>/<slug>` | `max-w-5xl` | `max-w-5xl` | KEEP | `templates/events/event_detail.html:18` | Detail tier |
| `/events/series/<id>/<slug>` | `max-w-5xl` | `max-w-5xl` | KEEP | `templates/events/event_series.html:33` | Detail tier |
| Join countdown | `max-w-2xl` | `max-w-2xl` | PAD | `templates/events/join_countdown.html:13` | Narrow tier; `px-6 lg:px-8` drift |
| Join too early | `max-w-2xl` | `max-w-2xl` | PAD | `templates/events/join_too_early.html:9` | Narrow tier |
| Join unavailable | `max-w-2xl` | `max-w-2xl` | PAD | `templates/events/join_unavailable.html:9` | Narrow tier |
| `/events/<slug>/cancel-registration` | `max-w-lg` | `max-w-2xl` | CHANGE | `templates/events/cancel_registration_confirm.html:10` | Off-scale narrow; align with join-state siblings |
| Cancel registration result | `max-w-lg` | `max-w-2xl` | CHANGE | `templates/events/cancel_registration_result.html:9` | Same |
| `/events/<id>/host/manage` | `max-w-4xl` | `max-w-5xl` | CHANGE | `templates/events/host_management.html:9` | Only 4xl page on the site; management detail belongs in Detail tier like `/account/` |
| Host management denied | `max-w-2xl` | `max-w-2xl` | KEEP | `templates/events/host_management_denied.html:16` | Narrow tier status |

### Sprints and plans

| Route | Current | Proposed | Verdict | Template | Reason |
|---|---|---|---|---|---|
| `/sprints` | `max-w-5xl` | `max-w-7xl` | CHANGE | `templates/content/sprints_index.html:13` | Index page; every sibling listing is 7xl — the reported inconsistency vs `/events`. Also PAD (`px-6 lg:px-8`). Keep the `:14` intro column at `max-w-3xl` |
| `/sprints/<slug>` | `max-w-5xl` | `max-w-5xl` | KEEP | `templates/plans/sprint_detail.html:17` | Detail tier |
| `/sprints/<s>/board` | `max-w-5xl` | `max-w-5xl` | KEEP | `templates/plans/cohort_board.html:14` | Detail tier board |
| `/sprints/<s>/plans/<id>` | `max-w-5xl` | `max-w-5xl` | KEEP | `templates/plans/member_plan_detail.html:25` | Detail tier |
| `/sprints/<s>/plan/<id>` | `max-w-5xl` | `max-w-5xl` | KEEP | `templates/plans/my_plan_detail.html:26` | Detail tier |
| Sprint feedback form | `max-w-3xl` | `max-w-3xl` | KEEP | `templates/plans/sprint_feedback_fill.html:11` | Reader/form tier |
| Sprint feedback submitted | `max-w-3xl` | `max-w-3xl` | KEEP | `templates/plans/sprint_feedback_submitted.html:11` | Matches its form page |

### Voting, accounts, community

| Route | Current | Proposed | Verdict | Template | Reason |
|---|---|---|---|---|---|
| `/vote` | `max-w-7xl` | `max-w-7xl` | KEEP | `templates/voting/poll_list.html:12` | Index/listing |
| `/vote/<id>` | `max-w-5xl` | `max-w-5xl` | KEEP | `templates/voting/poll_detail.html:11` | Detail tier |
| `/accounts/login`, `/accounts/register` | `max-w-7xl` frame + `max-w-md` card | same | KEEP | `templates/accounts/includes/_auth_card.html:6-7` | Frame matches chrome; narrow inner card is intentional |
| `/accounts/password-reset-request` | `max-w-7xl` | `max-w-7xl` | KEEP | `templates/accounts/password_reset.html:11` | Frame correct (its `py-16 sm:py-24 lg:py-32` rhythm is a separate, non-width violation) |
| `/account/` | `max-w-5xl` | `max-w-5xl` | KEEP | `templates/accounts/account.html:18` | Detail tier |
| `/account/deleted` | `max-w-2xl` | `max-w-2xl` | KEEP | `templates/accounts/account_deleted.html:10` | Narrow tier |
| `/account/change-email/confirm` | `max-w-2xl` | `max-w-2xl` | KEEP | `templates/accounts/change_email_result.html:10` | Narrow tier |
| `/onboarding/` steps | `max-w-3xl` | `max-w-3xl` | KEEP | `templates/accounts/onboarding_start.html:11`, `onboarding_chat.html:11`, `onboarding_fill.html:13`, `onboarding_complete.html:11` | Multi-step form, Reader tier |
| `/notifications` | `max-w-5xl` | `max-w-5xl` | KEEP | `templates/notifications/notification_list.html:10` | Detail tier list |
| Slack join denied | `max-w-2xl` | `max-w-2xl` | PAD | `templates/community/slack_join_denied.html:9` | Narrow tier; `px-6 lg:px-8` drift |

## Prioritized change list

1. `/sprints` index to Frame tier — `templates/content/sprints_index.html:13`: `max-w-5xl px-6 lg:px-8` to `max-w-7xl px-4 sm:px-6 lg:px-8`, keeping the `:14` intro at `max-w-3xl`. This is the reported complaint and the most-trafficked offender; 1 file. Verify the sprint cards still read well at full width (they are the same full-width row shape as `/events` rows, which work at 7xl).
2. Host management to Detail tier — `templates/events/host_management.html:9`: `max-w-4xl ... px-6 py-12` to `max-w-5xl px-4 sm:px-6 lg:px-8` with a standard vertical rhythm; 1 file. Eliminates the only 4xl on the site.
3. Cancel-registration pages to Narrow tier — `templates/events/cancel_registration_confirm.html:10` and `cancel_registration_result.html:9`: `max-w-lg` to `max-w-2xl`; 2 files. Eliminates `max-w-lg` as an outer width.
4. Curated-link interstitial to Narrow tier — `templates/content/curated_link_verify_required.html:9`: `max-w-3xl` to `max-w-2xl` plus standard padding; 1 file. Low visual impact, pure rule consistency.
5. Padding normalization to `px-4 sm:px-6 lg:px-8` (no max-width change) — `sprints_index.html:13` (covered by item 1), `activities.html:16`, `workshop_page_detail.html:22`, `course_unit_detail.html:24` and `:125`, `email_app/subscribe.html:11`, `join_countdown.html:13`, `join_too_early.html:9`, `join_unavailable.html:9`, `slack_join_denied.html:9`, `peer_review/submit.html:12`, `peer_review/review_form.html:11`, `peer_review/certificate.html:11`; ~11 files. Mobile-only alignment fix, can ship as one mechanical batch.
6. Codify the Narrow tier — add `mx-auto max-w-2xl px-4 sm:px-6 lg:px-8` and its rule to the Spacing and Layout section of `_docs/design-system.md`; 1 file.

Total: 5 templates change their outer max-width, ~46 user-facing page templates keep theirs, ~11 templates get a padding-only touch, plus one design-system doc addition.

## Studio note (out of primary scope)

Studio pages use their own admin layout (`templates/studio/base.html`) and are exempt per `_docs/design-system.md:127`. They are internally inconsistent too: page containers range across `max-w-2xl` (27 uses), `max-w-3xl` (39), `max-w-4xl` (37), `max-w-5xl` (3), and the sync dashboard at `max-w-6xl` (`templates/integrations/admin_sync.html:7`, `admin_sync_history.html:7`) — the only 6xl. If Studio is ever normalized, that is a separate issue; it does not affect members.

## Open PM questions

- Header/footer chrome mobile gutters differ from the page standard (`header.html:9` uses `px-6`, `footer.html:8` uses `px-5`, pages use `px-4`). Aligning chrome gutters with the page gutter is a design-system decision, not covered by this audit's change list.
- Inner grids on `/activities` (`activities.html:45` and `:107`) use `max-w-6xl` inside the 7xl frame. It reads fine and is an inner element, not an outer frame, but 6xl is not a sanctioned width; decide whether to fold it into the frame width or bless it.

## Implementation record

Implemented 2026-07-21, directly in the working session rather than through the PROCESS.md pipeline (explicit user instruction). 22 templates touched.

### Width tier changes (7 templates)

| Template | Before | After | Reason |
|---|---|---|---|
| `templates/content/sprints_index.html` | `max-w-5xl` | `max-w-7xl` | The reported complaint: an index page inset ~200px from the chrome while all 11 siblings were 7xl |
| `templates/events/host_management.html` | `max-w-4xl` | `max-w-5xl` | Only 4xl on the site |
| `templates/events/cancel_registration_confirm.html` | `max-w-lg` | `max-w-2xl` | Off-scale |
| `templates/events/cancel_registration_result.html` | `max-w-lg` | `max-w-2xl` | Off-scale |
| `templates/email_app/verify_result.html` | `max-w-lg` | `max-w-2xl` | Off-scale; missed by the manual audit |
| `templates/email_app/unsubscribe_result.html` | `max-w-lg` | `max-w-2xl` | Off-scale; missed by the manual audit |
| `templates/content/curated_link_verify_required.html` | `max-w-3xl` | `max-w-2xl` | Status interstitial belongs in Narrow tier |

### Gutter normalization to `px-4 sm:px-6 lg:px-8` (15 templates)

`activities.html`, `workshop_page_detail.html`, `course_unit_detail.html` (two containers), `email_app/subscribe.html`, `join_countdown.html`, `join_too_early.html`, `join_unavailable.html`, `community/slack_join_denied.html`, `peer_review/submit.html`, `peer_review/review_form.html`, `peer_review/certificate.html`, plus three the manual audit missed: `accounts/password_reset.html`, `content/peer_review/dashboard.html`, `events/host_management_denied.html`. The width changes above also carried their gutters to standard.

### What the manual audit missed

The route table was assembled by reading templates. Four offenders were only caught once the test scanned every template that extends `base.html` and reported its first `mx-auto max-w-*` container:

- Two `max-w-lg` status pages in `email_app/` (`verify_result`, `unsubscribe_result`) — the same off-scale bug as the cancel-registration pair, in an app the audit did not walk.
- Three gutter-drift containers not in the padding list (`password_reset.html`, `peer_review/dashboard.html`, `host_management_denied.html`).

The lesson is in the guard design: automatic discovery over an enumerated list. A route table goes stale the moment someone adds a page; the discovery test does not.

### Test coverage

`content/tests/test_container_widths.py`, tagged `core`, six tests:

| Test | Catches |
|---|---|
| `test_page_containers_use_a_sanctioned_width` | Any page template using a width outside the four tiers. Auto-discovers pages, so new pages are covered without registration |
| `test_audited_pages_keep_their_assigned_tier` | Silent re-tiering of the specific pages this audit moved |
| `test_page_containers_use_the_standard_horizontal_gutter` | The `px-6 lg:px-8` drift (missing mobile `px-4` step) |
| `test_discovery_finds_the_known_page_templates` | The discovery regex silently matching nothing and passing vacuously |
| `test_no_stale_registry_entries` | Exemption entries outliving the templates or conditions that justified them |
| `test_container_matcher_self_check` | The matcher itself: chrome nav must not be read as the page frame; non-centered wrappers are not frames |

Mutation-verified on 2026-07-21 — reverting `/sprints` to `max-w-5xl`, setting an unsanctioned `max-w-4xl`, and reintroducing the `px-6 lg:px-8` gutter each produce a distinct failure naming the file and the expected value.

A second layer already existed: `playwright_tests/test_container_widths_525.py` asserts rendered width and mobile non-overflow against a hand-maintained URL list. `/sprints` was never in that list, which is precisely how it drifted to `max-w-5xl` and shipped. It has been added (2026-07-21), and the whole file passes: 42 tests before the addition, 4 of 4 in the `sprints or events` selection after.

The two layers are complementary and both are worth keeping:

| Layer | Checks | Blind spot it covers |
|---|---|---|
| `content/tests/test_container_widths.py` (source lint, `core`) | Template class strings, auto-discovered | A new page nobody remembers to register |
| `playwright_tests/test_container_widths_525.py` (rendered, `local_only`) | Computed pixel width, mobile overflow | A sanctioned class that still renders wrong because of a wrapper or inherited style |

Note that the Playwright layer is `local_only` and is deselected by the core gate, so a shared-UI change needs it run explicitly.

### Deliberately not changed

- Chrome gutters (`includes/header.html:9`, `events/host_management_denied.html:8`) still use `px-6` against the page standard of `px-4`. Reconciling them is the open question below; both are exempted by name in `CHROME_GUTTER_EXEMPT` with that reason recorded.
- Inner `max-w-6xl` grids on `activities.html` (`:45`, `:107`). Inner elements, not outer frames; the guard checks frames only.
- Studio and `templates/integrations/` admin surfaces.

## Screenshots

Captured at 1440x900 on 2026-07-21, stored under `.tmp/designer-width-audit-desktop/` (untracked; regenerate with `scripts/capture_screenshots.py` if needed):

| File | Shows |
|---|---|
| `sprints_1440x900.png` | Offender: 5xl index inset from 7xl chrome, empty right side |
| `events_1440x900.png` | Baseline: 7xl index filling the chrome frame |
| `blog_1440x900.png` | 7xl index baseline |
| `blog_crisp-dm-for-ai_1440x900.png` | Reader tier 3xl prose column |
| `pricing_1440x900.png` | 7xl marketing frame |
| `subscribe_1440x900.png` | Narrow tier 2xl form |
| `faq_1440x900.png` | Reader tier 3xl |
