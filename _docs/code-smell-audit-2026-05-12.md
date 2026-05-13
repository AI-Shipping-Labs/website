# Code Smell Audit - 2026-05-12

This audit looked for structural risk rather than one-off style nits: large modules, oversized functions, weak boundaries, broad exception handling, fragile tests, and places where implementation detail has leaked across layers.

## Executive Summary

The codebase is productive but shows clear signs of issue-by-issue accretion. Most risk is concentrated in GitHub content sync, Studio admin views, payment/webhook handling, and rich page views. The project has a large test suite, but many tests assert rendered strings or HTTP status codes, so they can preserve current behavior while missing deeper regressions in domain rules.

Ruff currently passes, but `pyproject.toml` only enables `F`, `I`, and `PLC0415`. That means the automated checks do not currently flag complexity, broad `except Exception`, long functions, duplicated code, mutable complexity, or test quality problems.

## Repository Metrics

- Python application/test code: about 238k lines across 430 production Python files and 498 test Python files.
- Largest production Python files:
  - `integrations/services/github_sync/dispatchers/courses.py`: 1,374 lines.
  - `studio/views/sync.py`: 1,248 lines.
  - `studio/views/users.py`: 967 lines.
  - `content/views/courses.py`: 965 lines.
  - `payments/services/__init__.py`: 932 lines.
  - `integrations/services/github_sync/orchestration.py`: 852 lines.
- Largest templates:
  - `templates/accounts/account.html`: 841 lines.
  - `templates/events/event_detail.html`: 655 lines.
  - `templates/studio/base.html`: 641 lines.
  - `templates/home.html`: 628 lines.
  - `templates/base.html`: 614 lines.
- Largest JavaScript file:
  - `static/js/studio/plan_editor.js`: 960 lines.

## Highest-Risk Hotspots

### 1. GitHub content sync is doing too many jobs

Files:

- `integrations/services/github_sync/orchestration.py`
- `integrations/services/github_sync/dispatchers/courses.py`
- `integrations/services/github_sync/dispatchers/workshops.py`
- `integrations/services/github_sync/dispatchers/events.py`

Evidence:

- `sync_content_source` is 285 lines and handles locking, HEAD skip logic, queue log adoption, repo clone/pull, file count guards, S3 upload, tier sync, classification, dispatch, source status mutation, failure handling, temp cleanup, and follow-up queueing.
- `_classify_repo_files` is 209 lines and encodes repository ownership rules, routing priority, YAML/Markdown parsing fallback behavior, malformed file handling, and path conventions.
- `_sync_single_course` is 234 lines and mixes YAML validation, slug collision handling, README fallback parsing, access-level parsing, Course upsert, orphan FK reattachment, instructor M2M handling, and module sync.
- `_sync_module_units` is 328 lines and is the largest function found.

Why this is risky:

- Sync correctness depends on implicit ordering between classifier, dispatchers, stale cleanup, and FK reattachment.
- Small feature changes can accidentally alter cleanup behavior or double-claim files.
- Error handling is difficult to reason about because some parse errors are terminal, some are accumulated into `stats['errors']`, and some are swallowed into warnings.
- Tests have to duplicate huge fixture setup to reach one behavior.

Recommended remediation:

1. Split sync into explicit pipeline objects or functions: lock/queue lifecycle, repo acquisition, classification, media upload, dispatch, stale cleanup, and final log write.
2. Introduce typed result objects for classification and dispatch stats instead of mutable dictionaries.
3. Move course/workshop sync into smaller units: parse frontmatter, resolve identity, upsert primary object, sync children, cleanup stale objects.
4. Add characterization tests around the current file-claiming order before refactoring.

### 2. Broad `except Exception` is common in core paths

Evidence:

- The scan found 132 `except Exception` handlers outside migrations and generated/static directories.
- Concentrated areas include:
  - `integrations/services/github_sync/*`
  - `payments/services/__init__.py`
  - `events/views/api.py`
  - `accounts/views/auth.py`
  - `analytics/middleware.py`
  - `studio/views/*`

Why this is risky:

- Broad catches often turn programmer errors into partial syncs, warnings, empty fallbacks, or user-facing success paths.
- Operationally important failures can be hard to distinguish from expected external-service failures.
- Some handlers log only `str(e)`, losing traceback and error type.

Recommended remediation:

1. Replace broad catches around parsers, network clients, and DB operations with specific exception classes.
2. Where broad catches are intentionally defensive, log with `logger.exception` or include structured error type/context.
3. Add a lint rule such as Ruff `BLE001` once intentional cases are annotated.

### 3. Payments service is a package-sized module hidden in `__init__.py`

File:

- `payments/services/__init__.py`

Evidence:

- 932 lines, 27 top-level functions.
- Handles Stripe client access, checkout creation, checkout completion, subscription changes, cancellation, course purchase, attribution, email, and webhook-side state transitions.
- Contains many broad exception handlers around Stripe and lifecycle paths.

Why this is risky:

- Importing `payments.services` brings a large amount of unrelated behavior into one namespace.
- Payment lifecycle code is high-impact and should have narrow modules with explicit responsibilities.
- `__init__.py` hides the real module shape and makes circular dependencies more likely.

Recommended remediation:

1. Keep compatibility re-exports in `payments/services/__init__.py`.
2. Move implementation into modules such as `checkout.py`, `subscriptions.py`, `course_purchases.py`, `webhooks.py`, and `attribution.py`.
3. Tighten exception types around Stripe API failures versus local data consistency failures.

Current product-model correction:

The current Stripe model is external Payment Links plus Stripe webhooks back into this platform. Under that model, several local Stripe API paths are now unnecessary or misleading:

- `payments/views/checkout.py` still exposes local APIs for creating Checkout Sessions, upgrading subscriptions, downgrading subscriptions, and cancelling subscriptions.
- `payments/services/__init__.py` still creates Checkout Sessions and directly mutates Stripe subscriptions through `create_checkout_session`, `upgrade_subscription`, `downgrade_subscription`, and `cancel_subscription`.
- `accounts/views/account.py` still has `cancel_subscription_view`, which calls the local cancellation service and then sets local pending state.
- `templates/accounts/account.html` still contains modal/UI code for local upgrade, downgrade, and cancel flows when checkout is enabled.
- `content/views/courses.py` still has `api_course_purchase`, which creates a one-time Stripe Checkout Session for course purchases.
- `studio/views/courses.py` still has `course_create_stripe_product`, which creates Stripe products/prices from Studio.

Target shape:

1. Keep pricing-page Payment Links and the Stripe Customer Portal link.
2. Keep webhook signature verification, idempotency, and handlers needed to register/update users from Stripe events.
3. Keep Stripe import/backfill tools if they are still useful for repair/reconciliation.
4. Remove or hard-deprecate local Checkout Session creation, direct subscription mutation APIs, local cancellation APIs, and course-purchase Stripe product/session creation.
5. Replace tests for removed local-payment flows with tests for Payment Link rendering, prefilled email behavior, webhook registration, webhook idempotency, subscription update/deletion handling, and portal-link presence.

### 4. Studio user listing does filtering in Python after loading every user

File:

- `studio/views/users.py`

Evidence:

- `_build_user_listing` loads `list(User.objects.select_related('tier').all())`.
- Filtering by search, tag, tier, Slack membership, and active override is then done in Python.
- Counts are recomputed by repeatedly iterating over the full in-memory list.

Why this is risky:

- This is fine with small data and degrades sharply as the user table grows.
- CSV export and HTML listing share the same full materialization path.
- It increases memory use and makes future filters more likely to be bolted on in Python.

Recommended remediation:

1. Push simple filters into QuerySets: subscription, Slack membership, names/email/Stripe/Slack ID.
2. Use annotations/subqueries for active tier overrides.
3. Keep tag filtering in Python only if the JSON/list field cannot be queried portably.
4. Compute counts with database aggregation where possible.

### 5. Course unit page view mixes access policy, teaser generation, drip scheduling, progress, navigation, and rendering

File:

- `content/views/courses.py`

Evidence:

- `course_unit_detail` is 281 lines.
- It includes legacy anonymous access exceptions, gating CTA copy, teaser generation, homework teaser extraction, secondary signup CTA logic, cohort drip-lock logic, progress lookup, previous/next navigation, discussion visibility, and mobile reader progress context.

Why this is risky:

- Access policy is spread across conditionals and presentation copy.
- Legacy exceptions are easy to break because they are embedded inside one large view.
- Testing has to assert template output to prove policy behavior.

Recommended remediation:

1. Extract an access decision object with reason, status code, CTA metadata, and template context.
2. Extract navigation/progress context building into a small service.
3. Keep the Django view as orchestration only: load objects, ask services for decisions/context, render.

### 6. The plan editor JavaScript is a full client application in one file

File:

- `static/js/studio/plan_editor.js`

Evidence:

- 960 lines in a single IIFE.
- Owns bootstrap parsing, save indicator state, toast state, API retry/revert behavior, debounced fields, SortableJS integration, keyboard movement, inline editing, add/delete flows, and DOM rendering details.

Why this is risky:

- UI state, API state, and DOM mutation are interleaved.
- Optimistic updates and retries are hard to test in isolation.
- Future behavior changes can break keyboard support or revert behavior unintentionally.

Recommended remediation:

1. Split into small modules if the build setup supports it, or at least local sections with pure helpers for state transforms.
2. Isolate API/retry behavior from DOM manipulation.
3. Add browser tests around failed writes, retry success, keyboard move, and delete revert paths.

### 7. Templates are large and contain too much page logic

Files:

- `templates/accounts/account.html`
- `templates/events/event_detail.html`
- `templates/studio/base.html`
- `templates/home.html`
- `templates/base.html`
- `templates/studio/plans/_editor_body.html`

Evidence:

- Multiple templates exceed 600 lines.
- `templates/base.html` has 10 script tags.
- `templates/studio/plans/_editor_body.html` has 4 script tags.
- `templates/admin/widgets/timestamp_editor.html` has 15 inline style attributes.

Why this is risky:

- Large templates encourage copy/paste and make page behavior hard to review.
- Inline scripts/styles bypass reusable design-system patterns.
- Tests that assert rendered strings become brittle because markup is doing too much work.

Recommended remediation:

1. Extract repeated UI sections into includes/components.
2. Move page scripts into static JS files.
3. Prefer design-system classes over inline styles, especially in reusable admin widgets.

## Test Smells

The test suite is broad, which is good, but several patterns are suspicious:

- About 1,567 matches for status-code-only assertions.
- About 2,157 `assertContains(..., "literal")` style assertions.
- About 2,395 mock/patch-related matches.
- Several test files are extremely large:
  - `integrations/tests/test_github_sync.py`: 2,875 lines.
  - `studio/tests/test_sync_dashboard.py`: 1,963 lines.
  - `integrations/tests/test_workshop_sync.py`: 1,830 lines.
  - `events/tests/test_events.py`: 1,540 lines.
  - `playwright_tests/test_github_content_sync.py`: 1,505 lines.

Interpretation:

- Status-code-only tests are useful smoke tests, but weak regression guards.
- Literal rendered-string assertions can lock in markup without proving behavior.
- Heavy mocks in service tests can make tests pass even when integration boundaries drift.
- Large test modules often indicate duplicated fixture setup and missing helper factories.

Recommended remediation:

1. For critical flows, assert domain state changes, permissions, persisted records, emitted tasks, and visible user outcomes, not just status codes.
2. Extract shared factories/builders for sync, users, tiers, courses, and events.
3. Split large test modules by behavior area.
4. Add a small number of integration tests that avoid mocking internal service calls for payments and content sync.

See `_docs/test-suite-audit-2026-05-12.md` for the dedicated test-quality and runtime-optimization plan, including candidates to remove from default CI.

## Tooling Gaps

Current Ruff config is intentionally minimal:

- Enabled: `F`, `I`, `PLC0415`.
- Not enabled: complexity, broad exception, bugbear, simplify, unused arguments, return consistency, blind except, print/debug checks, test-style checks.

Suggested staged additions:

1. Add non-invasive checks first: `B`, `BLE`, `C4`, `SIM`, `RET`, `ARG`, `T20`.
2. Start with `--preview` reports or per-directory ignores instead of failing CI immediately.
3. Add complexity reporting with `radon` or Ruff McCabe (`C901`) in advisory mode.
4. Track max function length and broad exception count as audit metrics.

## Priority Refactor Plan

1. Characterize GitHub sync behavior with focused tests, then split orchestration and dispatch responsibilities.
2. Simplify Stripe integration around the real product model: Payment Links out, webhooks in, Customer Portal for self-service billing. Remove or deprecate local Checkout Session creation, direct subscription mutation APIs, local cancellation APIs, and course-purchase Stripe product/session creation.
3. Break the remaining `payments/services/__init__.py` webhook/import/attribution code into explicit lifecycle modules while preserving imports during migration.
4. Extract course-unit access/gating decision logic from the view.
5. Move Studio user filtering toward QuerySet/database-level filtering.
6. Split `plan_editor.js` around API, state transitions, and DOM wiring.
7. Decompose the largest templates after the view/service boundaries are clearer.

## What Looks Suspicious But Not Urgent

- TODO count is low. Only two real TODOs were found outside specs/example strings.
- No obvious syntax errors were found by Ruff under the current configuration.
- The broad test count indicates active coverage work, but the shape of the tests suggests the suite may be expensive to maintain and may still miss behavior regressions in the most complex services.
