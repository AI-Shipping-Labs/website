# Test Suite Audit — 2026-08-13

Supersedes `2026-05-12-test-suite-audit.md`, which was removed as stale (its
recommendations — marker adoption, screenshot-suite quarantine, Playwright sharding —
were implemented; the items that were not are carried forward below).

Companion document: `_docs/audits/2026-08-13-studio-ux-ui-audit.md` covers Studio
information architecture and visual uniformity. The two audits share a root cause,
described in section 4.

## 1. Scope and method

Six parallel audits read the test bodies — not just names — across the whole suite,
each producing three lists: tests that should be combined, tests that cannot fail, and
missing coverage. Every finding below names a file and a test. Claims of duplication
were made only where both sides were read.

| Scope | Files | Test functions |
|---|---|---|
| Playwright — public, guest, marketing, design polish | 83 | 429 |
| Playwright — studio | 118 | 617 |
| Playwright — member surfaces | 171 | 1,159 |
| Django — accounts, api, member_api, payments, crm, analytics | 208 | 3,497 |
| Django — events, plans, email_app, community, notifications, questionnaires, bookclub, voting, comments, asl_cli | 214 | 3,058 |
| Django — content, integrations, studio, jobs, triggers, tests | 446 | 8,122 |
| Total | 1,240 | 16,882 |

Measured independently: 873 Django test files holding 14,614 test functions, and 386
Playwright files holding 2,480 collected tests. The per-scope counts above overlap
slightly at scope boundaries and count parametrized cases differently; treat them as
scope-relative, not as a suite census.

## 2. Headline numbers

| Metric | Value |
|---|---|
| Tests that cannot fail if the feature breaks | ~350 identified, ~290 in the Django scopes alone |
| Browser sessions removable by merging duplicated Playwright tests | ~250-300 per full run |
| Bare `status_code == 200` assertions | 121 in content and studio, plus more elsewhere |
| Bare `assert_called_once()` with no argument assertions | 87 |
| Untagged Tailwind/JS source-string tests gating push CI | 32 files |
| PBKDF2 password hashes per Django run | ~4,700 |
| Registered settings keys with no test at all | 10 of 131 |

## 3. CI cost and timing

All numbers from GitHub Actions runs on 2026-08-12 and 2026-08-13, using the `CI_TIMING`
instrumentation already emitted by `ci.yml` and `deploy-dev.yml`.

### 3.1 Deploy Dev — the critical path

`Deploy Dev` runs on every push to `main`. Representative run `31672275264`:

| Job | Wall time | Sharded |
|---|---|---|
| Deploy Gates (lint / migrations / OpenAPI / system check / static) | 48s | n/a |
| PostgreSQL 16 Verification | 3min | no |
| Unit & Integration Tests shard 1/4 | 12min 35s | yes, 4x |
| Unit & Integration Tests shard 2/4 | 15min 36s | yes, 4x |
| Unit & Integration Tests shard 3/4 | 13min 45s | yes, 4x |
| Unit & Integration Tests shard 4/4 | 11min 43s | yes, 4x |
| Playwright Core E2E | 21min 37s | NO |
| Deploy to Dev | 6min | n/a |

Pure test-runner seconds, excluding checkout, uv, browser install and DB cache restore:

| Shard | Tests | Runner seconds |
|---|---|---|
| 1/4 | 3,696 | 701.4 |
| 2/4 | 3,624 | 884.4 |
| 3/4 | 3,636 | 774.4 |
| 4/4 | 3,602 | 648.3 |
| Django total | 14,558 | 3,008s (50min) |
| Playwright core | 905 | 1,297s (22min) |

Observed end-to-end wall clock: 29 minutes, consistently. Total runner minutes per push:
approximately 87.

### 3.2 The binding constraint

```
max(15min worst Django shard, 22min Playwright Core) + 6min deploy + ~1min gate = 29min
```

`Playwright Core E2E` is a single unsharded, unparallelized invocation:

```
uv run pytest -m "core and not manual_visual and not slow_platform and not visual_regression" playwright_tests/ -v
```

No `-n`, despite `pytest-xdist>=3.8.0` being a declared dependency. No matrix.

Meanwhile `.github/workflows/scheduled-playwright.yml` lines 69-152 already implement
exactly the sharding this job needs: a 4-entry matrix that round-robins the Playwright
file list with `awk '((NR - 1) % total) == shard'`. That mechanism is proven in
production and is simply not applied to the deploy-critical job.

Applying it moves the critical path from Playwright to the worst Django shard, taking
`Deploy Dev` from 29 minutes to roughly 21. At 10-14 pushes per day that is 80-110
minutes per day of removed waiting for a configuration change that copies an existing
block.

Sharding cuts wall clock, not cost. Runner minutes stay flat. Cutting spend requires the
deletions in section 7.

### 3.3 Scheduled suites

| Workflow | Cron | Runs/day | Wall | Runner min/day |
|---|---|---|---|---|
| Scheduled Playwright (full suite) | `0 */3 * * *` | 8 | ~17min | ~464 |
| Scheduled Playwright (dev environment) | `30 1-23/3 * * *` | 8 | ~3min | ~24 |

Full-suite shards are balanced by file index, not duration, so shard 3 finishes 3 minutes
before shard 2 on equal test counts. Duration-balancing recovers 2-3 minutes per run.

Estimated total spend at ~12 pushes/day: approximately 1,530 runner-minutes/day, or 25
runner-hours/day.

### 3.4 No per-test timing exists

`[tool.pytest.ini_options]` in `pyproject.toml` declares markers but sets no `addopts`,
so `--durations` is never passed. The Django runner prints only `Ran N tests in Xs`. No
CI log attributes runtime to an individual test or file.

Every runtime claim in this audit is therefore inferred from call counts (`page.goto`,
`create_user`, browser contexts), not measured. Adding `--durations=25` should precede
the next optimization pass.

### 3.5 Suite reliability

`Scheduled Playwright (full suite)`, last 30 runs: 16 success, 13 failure, 1 cancelled.

The failures are one contiguous red window from `2026-08-12T02:32Z` to
`2026-08-13T07:14Z` — 13 consecutive failures over 29 hours, fixed by `89faea57` and the
`#1415` / `#1418` merges. This is a resolved incident, not a flaky suite. It is recorded
here only for the detection latency: alerting fired on every failing run, so the gap is
in how quickly a filed failure issue is picked up.

## 4. Cross-cutting root causes

Four patterns produced most of the findings. Fixing the patterns matters more than
fixing any individual test.

### 4.1 Per-issue file accretion

Issue #1241 gets `test_homepage_ia_1241.py`. Issue #1235 gets
`test_homepage_tier_layout_1235.py`. Each file is correct when written and traceable to
a ticket. Nobody checks whether the assertions already exist elsewhere, because the new
file passes CI either way.

The homepage tier-carousel contract — 3 cards, no free card, Main centered, no overflow
— is now asserted in seven files: `test_homepage_funnel_1162`, `test_homepage_ia_1241`,
`test_homepage_visual_1241`, `test_homepage_tier_layout_1235`,
`test_membership_carousel_layout`, `test_tier_carousel_510`, `test_pricing_layout_1188`.

Two costs. Seven browser launches for one contract. And when the carousel legitimately
changes, seven files go red and someone must work out which still encode an intentional
requirement.

The same shape appears in `test_studio_*_NNNN.py`, the ten `test_book_club_*` files, the
four `test_account_*` files, and the plans `583/584/733` cluster.

### 4.2 Wrong-layer testing

Playwright tests that never exercise the browser, and Django tests that assert rendered
CSS. Both directions are present.

Rule 10 violations — Playwright files with no meaningful navigation:
`test_event_zoom_lifecycle_1074.py` (3 `core`-marked API tests),
`test_sprint_progress_evidence_api.py`, 8 of 10 in `test_events_calendar_feed.py`
(urllib, no browser), 11 of 13 in `test_monthly_payment_grace_1413.py`,
`test_complete_finished_events.py` (invokes the Django task directly).

The reverse: 32 Django/Playwright files assert Tailwind class substrings without the
`visual_regression` tag, so they gate push CI contrary to the project's own policy —
including `events/tests/test_events_mobile.py` and `payments/tests/test_pricing_mobile.py`
in their entirety.

Cross-layer duplication where the Django test should win: ~33 studio test-pairs,
`playwright_tests/test_account_page.py` versus `accounts/tests/test_account.py`,
`test_event_url_canonicalization_673.py`, `test_sprint_ended_join_1233.py`,
`test_sprint_plan_goal_584.py`.

### 4.3 Assertions that cannot fail

Three recurring shapes.

Substring assertions whose haystack always contains the needle:
`test_access_control.py` asserts `"Main" in body` on a page whose title contains
"Main"; four onboarding tests assert `"plan" in page.content().lower()` where the
template yields 34 spurious matches.

Mock theatre — `assert_called_once()` with no argument check, 87 sites. Sharpest:
`studio/tests/test_banner_upload_views.py`, where deleting the wrong S3 key passes.

Tests asserting their own code — `test_s3_enabled_setting.py` re-implements the
production line inside the test body.

Vacuous setups — `users_name_layout` asserts `count == 0` for a user that is never
seeded, so it passes whatever the page does.

### 4.4 Enforcement is opt-in

This is the same root cause identified in the Studio UX audit, and it explains why two
items from the 2026-05-12 audit were never actioned and have since spread.

The May audit flagged source-string-inspection tests. They were not removed, and the
pattern spread from `plans/` into three `api/tests/` files and two new `plans/` files.
It also flagged `setUpTestData` and fast-hasher adoption; neither happened.

Nothing in CI fails when a new test violates a documented rule. `_docs/testing-guidelines.md`
is advisory. The guards that do exist are scoped to hardcoded allowlists — see the
Studio audit section 8.1 for the same mechanism in templates.

## 5. Recommendations

Ordered by return per unit of work. Phase 0 items are cheap, mechanical, and carry no
product risk.

### Phase 0 — configuration only, no test edits

| # | Change | Effect | Size |
|---|---|---|---|
| 1 | Set a fast `PASSWORD_HASHERS` for the test settings | ~4,700 PBKDF2 hashes per run become trivial. `create_user(` appears 3,373 times and `client.login(` 1,321 times; there is no global override in `website/settings.py` or `website/test_runner.py`, and only a handful of files opt in locally. Likely the single largest Django runtime lever in this audit | S |
| 2 | Shard `Playwright Core E2E` 4x, copying `scheduled-playwright.yml:69-152` | Deploy Dev 29min to ~21min | S |
| 3 | Add `--durations=25` to the pytest invocations | Makes every later optimization measurable rather than inferred | S |
| 4 | Duration-balance the scheduled full-suite shards | 2-3min per run | S |

Item 1 should be measured before and after — the arithmetic suggests a large fraction of
the 3,008s Django runner total, but section 3.4 explains why no measurement exists yet.

### Phase 1 — enforcement ratchet

Add lint tests that scan by directory rather than by allowlist, each seeded with a
`KNOWN_EXCEPTIONS` set containing exactly today's failures so CI is green on day one,
and each asserting the exception set only shrinks:

- No new `status_code == 200`-only assertion in a view test.
- No `assert_called_once()` without an argument assertion.
- No Tailwind class substring assertion outside a `visual_regression`-tagged test.
- No source-string inspection (`inspect.getsource`, reading `.py` files to grep them).
- Playwright files must contain at least one `page.goto` or fixture navigation.

Without this, sections 6 and 7 get re-audited in three months. With it, each cleanup
sweep permanently retires a slice of the exception list, and the list becomes the live
progress tracker.

### Phase 2 — deletions

Section 7. ~290 Django tests and the dead Playwright files. Pure subtraction, no
behavior change, immediate runtime return.

### Phase 3 — merges

Section 6. Higher effort and higher risk than deletion because merged tests must retain
every distinct assertion. Highest-value targets first: `test_account_page.py`,
`test_membership_tiers.py`, the two studio header matrices, `studio/tests/test_access.py`.

### Phase 4 — coverage gaps

Section 8, prioritized by the product bugs in section 9.

## 6. Combine

### Playwright — public and guest

| Finding | Files | Action |
|---|---|---|
| Homepage tier-carousel contract asserted 7x | `test_homepage_funnel_1162`, `_ia_1241`, `_visual_1241`, `_tier_layout_1235`, `test_membership_carousel_layout`, `test_tier_carousel_510`, `test_pricing_layout_1188` | Keep `funnel_1162` + `ia_1241` |
| 34 tests each paying own `goto` for 1-2 assertions; ~26 internal duplicates including the verbatim pair `test_only_main_tier_has_most_popular_badge` / `test_main_tier_has_most_popular_badge` | `test_membership_tiers.py` | Reduce to ~8 |
| Billing toggle tested in 4 files | — | `test_pricing_billing_toggle_541.py` sole authority |
| 2 of 5 tests duplicated verbatim; login scenarios 7-9 verbatim copies of logout file | `test_login_submit_feedback.py`, `test_auth_shared_components.py`, `test_login_return_context.py`, `test_logout_return_context.py` | Deduplicate |
| Footer-newsletter suppression 5x; mobile drawer taxonomy 4x | — | One authority each |
| Fully superseded | `test_testimonials_layout.py` by `test_testimonial_heights_511.py` | Delete |

### Playwright — studio

Scale: ~605 browser contexts per run. 60 files carry `core`; the two header-matrix files
alone hold ~79 core nodes, over half the documented 100-150 whole-suite core budget.

| Finding | Files | Saving |
|---|---|---|
| `stacked_headers_1274` (31-route parametrize) and `detail_headers_1275` (37-route) burn one context per route for a per-route contract that `test_studio_list_baseline_1193` already covers for 10 pages in ONE session | those three | 66 contexts |
| Sidebar tested across 3 files; drawer open/close 4x, section order 2x, Users-single-anchor 2x | `sidebar_layout`, `sidebar_mobile_density`, `sidebar_reorg` | ~45 to ~10 |
| Grant/revoke/highest-tier each tested twice with same testids | `user_detail_layout_586` vs `user_detail_tier_override` | Keep `tier_override` (has DB asserts) |
| Settings-filter test near-verbatim duplicate | `findability_1287` vs `settings_sections` | Deduplicate |
| ~34 per-file 403/anonymous-gate tests duplicate `studio/tests/test_access.py` | — | Django authoritative |
| Server-side querystring paging tested through the browser, 8 sessions, 6x60 seeded users | `signup_analytics_pagination` | Move to Django |

### Playwright — member surfaces

| Finding | Files | Saving |
|---|---|---|
| 42 tests each paying own context + `goto /account/` for 1-2 assertions; contains an exact duplicate pair and duplicates two other files assertion-for-assertion | `test_account_page.py`, `test_account_cleanup_581.py`, `test_account_effective_tier_965.py` | ~30 sessions; merge to ~12 persona journeys |
| 4 of 5 tests subsumed; free/anon gate recipe re-implemented in 6 book-club files | `test_book_club_1362.py` + 5 others | One parametrized gate matrix |
| Join journey, ended-sprint copy, badges, `/activities` redirect, teammate read-only plan (4 files), owner inline-edit (3 files) each asserted multiply | `test_sprint_detail_981`, `test_sprint_landing_1242`, others | Consolidate |
| Register/cancel/gate flows asserted 3-5x; `test_event_series.py` spends 14 staff sessions on one Studio page | `test_events_calendar.py` (legacy, fully overtaken), `test_event_detail_registration.py`, series files | Large |
| Copy-paste tier matrix with zero parametrize | `test_access_control.py` | Parametrize |
| 21 contexts for what needs ~7 | `test_newsletter_only_gating_769.py` | 14 contexts |

### Django

| Finding | Files | Action |
|---|---|---|
| ~90 methods across 33 classes re-prove the same `@staff_member_required` decorator | `studio/` | One `subTest` matrix in `studio/tests/test_access.py`, itself 27 one-URL methods today |
| 15 Django-vs-Playwright studio pairs assert the same server-rendered behavior | — | Django wins ~33 pairs; Playwright wins ~7 geometry/dialog tests |
| 401/405 matrix re-asserted for endpoints already walked by the matrix test | `api/tests/test_sprints.py::SprintsAuthTest`, `test_plans.py::PlansAuthTest`, `test_plans_auth_matrix.py` | Keep the matrix, delete both |
| `customer.subscription.deleted` field-clearing tested field-for-field in two files, 4 duplicated tests each, one field per test with rebuilt fixtures | `payments/tests/test_webhooks.py`, `test_progress_retention.py` | Merge |
| 8 tag-normalization classes, 6 studio-edit-button classes, ~120 per-enum methods | `content/`, `test_video_player.py`, `test_downloads.py` | `subTest` tables |
| 9 registry-key classes duplicate one registry walk; 9 one-test classes with copy-pasted `mkdtemp` setUp; `sync_fixtures.py` bypassed by 18 of 30 temp-repo files | `integrations/`, `test_sync_head_sha_skip.py` | Consolidate |
| 14 per-schedule methods re-run `setup_schedules`, ~500 DB writes per class | `jobs/` | `subTest` |
| Cancelled-event visibility asserted 3x; zoom-link window truth-tabled then re-walked; vevent semantics asserted twice; viewer timezone re-fixtured 4x | `events/` | Consolidate |
| Carry-over compaction duplicated service+view; visibility matrix at 3 layers; owner page fixture-rebuilt in 4 issue files | `plans/` | Consolidate |
| Removal/reactivate tasks tested identically | `community/tests/test_tasks.py`, `test_services.py` | Merge |

## 7. Useless

Approximately 350 tests. ~290 in the Django scopes, of which ~220 are delete and ~70 are
strengthen.

### Delete

| Category | Examples | Count |
|---|---|---|
| Framework behavior (Rule 3/16) | `integrations/tests/test_github_sync.py::SourceTrackingFieldsTest` (9 CharField round-trips), `content/tests/test_access_control.py::RequiredLevelFieldTest` (5 default-0 tests), `events/tests/test_events.py::EventModelFieldsTest`, voting round-trips, trivial `__str__` in bookclub/questionnaires | ~60 confirmed |
| `unique=True` IntegrityError tests | plans, questionnaires, email_app | 8 |
| strftime wrappers | `accounts/tests/test_date_formatting.py` | 4 |
| Framework tests in one file | `email_app/tests/test_email_log.py` — 10 of 11 (ordering, defaults, `related_name`) | 10 |
| Zero-assertion tests | `test_slack_signal.py` ("should not raise"), `test_set_slack_member_tag_is_idempotent`, `test_home_peer_tiers_reachable_via_swipe` (scrolls and screenshots, asserts nothing) | — |
| Import-only smoke | `test_users_mark_bounced.py` | 1 |
| Dead / permanently skipped | `test_slack_integration.py` (hits real Slack, always skipped in CI) | file |
| Asserts a retired route | `test_event_recap.py` | — |
| Re-reads its own writes | `test_event_series.py::test_studio_event_survives_sync_dashboard_visit` | 1 |
| Asserts its own code | `test_s3_enabled_setting.py::S3DisabledErrorFlowsToSyncLogPartialTest` | 1 |
| Pins documentation prose | `test_zoom_documentation.py` (asserts `'roughly 55'`) | — |
| f-string restatement, tagged `core` | `get_studio_edit_url` tests | — |

### Strengthen

| Issue | Location | Why it cannot fail |
|---|---|---|
| Bare `status_code == 200` | 58 content, 58 studio, plus `test_tier.py`, `test_cleanup_gates.py`, `test_boot_timing.py`, `test_pricing_page_loads_without_login` (core-marked) | A paywall regressing to an empty 200 is invisible |
| `assert_called_once()` with no argument assertion | 87 sites; worst `studio/tests/test_banner_upload_views.py` (wrong S3 key passes), S3 `upload_file` in `test_github_sync.py`, `community/tests/test_tasks.py` | Asserts the mock, not the behavior |
| Substring always present | `test_access_control.py` (`"Main" in body`, plus a re-copied `iframe` false positive), 4 onboarding tests (`"plan" in content` — 34 spurious matches) | Passes regardless of feature state |
| Vacuous fixture | `users_name_layout` asserts `count == 0` for a never-seeded user | Passes whatever the page renders |
| Never confirms the action | worker retry/delete in `action_cells` only dismisses the dialog and checks a border class | The destructive action is unverified |
| Rule 12 no-side-effect gaps | `EmailPreferencesAPITest`, register/unregister/comment auth rejections assert only status | Rejection could still mutate |
| Conditional guard that silently skips | `test_dark_mode_contrast.py` | Skips instead of failing |
| Frozen baseline | 1449px in `tier_carousel_510` | Environment-dependent |

### Mis-tagged, gating push CI against policy

32 files assert Tailwind or JS source strings without `visual_regression`. Entire files:
`events/tests/test_events_mobile.py`, `payments/tests/test_pricing_mobile.py`, most of
`test_plan_body_chrome_733.py`, `test_sprint_detail_981.py::TestSprintDetailDesignSystem1138`,
`container_widths_525`. Plus `test_action_buttons_1280.py` firing 18 screenshots inside
the core deploy gate, `detail_headers_1275`'s 36-navigation screenshot loop marked
`core`, `test_mermaid_theme_screenshots.py` and `test_mermaid_style.py` (screenshot-only,
no assertion), and `test_member_surfaces_543.py` (20 contexts duplicating its own smoke).

Also: 18 hardcoded `wait_for_timeout` calls in the public scope, 6 more
`wait_for_timeout`-then-assert-nothing patterns in studio, 9 needless `/admin/login/`
form logins in `github_content_sync`, and 26 `core`-tagged subprocess deploys in
`tests/test_deploy_dev_grace_poll.py`.

### Runtime offenders

| Location | Problem |
|---|---|
| Suite-wide | No fast `PASSWORD_HASHERS`; ~4,700 PBKDF2 hashes per run |
| `payments/tests/test_progress_retention.py` | Rebuilds a course graph ~29 times via `setUp` across 5 classes |
| `notifications/tests/test_service.py::NotificationServiceNotifyTest` | ~126 creates per run |
| voting `PollDetailViewTest`, `VoteToggleAPITest` | Heavy per-test `setUp` |
| `CheckoutConcurrencySecurityTest` | Threads, sleeps, `reset_sequences`, no `slow_platform` tag |
| `tests/test_r1_migration_compatibility.py`, `integrations/tests/test_consolidation_migration.py` | Migration-executor `TransactionTestCase`s |
| community, email_app, notifications, voting | Skew heavily to `setUp` over `setUpTestData` |

## 8. Missing

### Highest priority

| Gap | Evidence |
|---|---|
| `sync_content` management command has zero tests anywhere | Verified by grep. Includes its `sys.exit(1)` failure signal and multi-source continue-on-error loop |
| SES SNS signature verification never exercised | `_verify_signature` and `_is_valid_cert_url` are patched out in every test |
| Zoom webhook has no timestamp-freshness check | Replayable |
| GitHub webhook has no delivery dedup | Double-enqueues sync |
| `triggers.is_eligible` rewritten to `return False` would pass the suite | No tiered test user exists |
| 10 of 131 registered settings keys appear in no test | All three `BANNER_UPLOAD_*` keys could be hardcoded without a failure |

### Product and payment states

Member-visible payment failure, grace and `past_due` states have no coverage and no
member-facing copy exists at all — today these are staff-report-only. Webhook-driven UI
transitions (R-PAY-5/6) are pinned nowhere. Unknown SES event kinds are untested. The
`PATCH unsubscribed:false` resubscribe path is untested. Privacy deletion does not pin
the fate of `CampaignVisit` / `UserAttribution` rows, so `SET_NULL` leaves `ip_hash`
orphans.

### Scheduling and lifecycle

Weekly series generation across a DST boundary is unpinned — only non-transition 7-day
spacing is tested. `is_regular_cadence` tests spring-forward (167h) but not fall-back
(169h). Reminders exclude cancelled events in code but only draft is tested.
Rejoin-after-leave plan privacy is untested. No direct bounce-state campaign-audience
test.

### Studio destructive actions with zero coverage

Worker failed-task bulk-retry and bulk-delete. `/studio/payments/stripe-webhooks/`
dashboard (#1314). Peer-review issue-certificates, form-batch and extend-deadline (only
the button label is asserted). Sprint accountability add/remove/randomize. Email-template
send-test. UTM link add/archive and campaign unarchive. All `remove-banner` routes.
Persona drag-reorder (#836, SortableJS — prime Playwright material). Studio-side Maven
email toggle. Seven studio write endpoints lack Rule 12 no-side-effect gates; `books.py`
has none at all. Only 1 of 10 studio trigger routes is gated.

### Member journeys

Logged-out register to login to return round trip. Unverified-email gate on write
actions. Workshop `landing_required_level` and level-30 gating, never exercised. The
`enroll_course` view — all tests ORM-seed enrollment instead. Emailed cancel-link page.
Tier-drop-after-enrollment on sprint boards.

### Public surfaces

Homepage blog section (`home-blog-card` and `home-blog-link` have zero coverage).
Branded 404 — no `templates/404.html` and only status-code tests. OS
`prefers-color-scheme` dark default, with `matchMedia` stubbed out. Dev smokes for
`/sprints`, `/interview`, `/tags`, `/subscribe`. Mobile drawer Escape and outside-tap
dismissal. Default `og:image`.

### Content and integrations

Downloads is the only content type without an orphan-cleanup test. Slug-collision
protection is tested only for Article. `content/workshop_facets.py` is dead code.
Triggers backoff schedule, lost-lease and IntegrityError duplicate-delivery guards are
dead code as far as tests are concerned.

## 9. Product bugs found

These are not test gaps. Each was verified against the source and warrants its own issue
independent of the test cleanup.

### 9.1 Campaign sends bypass the unsubscribe guard

`EmailService.send()` at `email_app/services/email_service.py:189` skips promotional
email to unsubscribed users. `send_campaign_batch` at
`email_app/tasks/send_campaign.py:216` never calls `send()` — it calls the private
`_send_ses()` (line 472) directly, bypassing the guard. The only `unsubscribed=False`
filter runs at `email_app/services/campaign_audience.py:30`, at fan-out time.

The batch loop re-fetches users and re-checks `email_verified` at send time, and its
comment states "the latest DB state wins... This mirrors how `unsubscribed` is handled".
It does not — `unsubscribed` is never re-checked in that loop. The comment asserts a
guarantee the code does not implement.

Effect: a user who unsubscribes after a campaign is enqueued but before their batch
executes still receives it. With chunked batches and per-send delays that window is
minutes to hours on a large campaign. This is a CAN-SPAM and GDPR exposure.

### 9.2 Refunds do not reach the application

The declared Stripe webhook contract is six events
(`payments/tests/test_stripe_webhook_observability.py:337`): `checkout.session.completed`,
`customer.subscription.updated`, `customer.subscription.deleted`,
`invoice.payment_failed`, `invoice.paid`, `customer.updated`.

`charge.refunded` has no handler. It appears in the codebase only inside that test, which
asserts it would be reported as an unexpected event if Stripe sent it. A refunded member
keeps their paid tier until the subscription separately lapses. Disputes are likewise
unhandled.

### 9.3 Delayed-payment buyers are never fulfilled

`checkout.session.async_payment_succeeded` is not in the handled set. Payment methods
that settle asynchronously complete successfully at Stripe and never grant access.

## 10. Carried forward from the 2026-05-12 audit

Implemented: marker adoption, screenshot-suite quarantine, Playwright sharding for the
scheduled workflow, obsolete local-Checkout test removal, webhook coverage build-out.

Not implemented, and since spread — these are the evidence for the Phase 1 ratchet:

- Source-string-inspection tests. Spread from `plans/` into three `api/tests/` files and
  two new `plans/` files.
- `setUpTestData` adoption over `setUp`.
- Fast password hasher.

That audit never covered the studio suite, and the per-issue file accretion described in
section 4.1 post-dates it.
