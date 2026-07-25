# Code Smell Audit - 2026-05-12

Note: slimmed on 2026-07-21. The Stripe "still expose local checkout" hotspot
(that code has since been removed or 410'd) and the stale large-module line-count
claim for `payments/services/__init__.py` (that module has since been split down
to under 100 lines) were removed. The remaining hotspots (1, 2, 4, 6, 7) are kept
with line counts refreshed against the current tree.

This audit looked for structural risk rather than one-off style nits: large modules, oversized functions, weak boundaries, broad exception handling, fragile tests, and places where implementation detail has leaked across layers.

## Executive Summary

The codebase is productive but shows clear signs of issue-by-issue accretion. Most risk is concentrated in GitHub content sync, Studio admin views, payment/webhook handling, and rich page views. The project has a large test suite, but many tests assert rendered strings or HTTP status codes, so they can preserve current behavior while missing deeper regressions in domain rules.

Ruff currently passes, but `pyproject.toml` only enables `F`, `I`, and `PLC0415`. That means the automated checks do not currently flag complexity, broad `except Exception`, long functions, duplicated code, mutable complexity, or test quality problems.

## Repository Metrics

- Largest production Python files (refreshed 2026-07-21):
  - `api/views/plans.py`: 2,392 lines.
  - `studio/views/users.py`: about 1,930 lines.
  - `api/views/users.py`: 1,886 lines.
  - `integrations/services/github_sync/dispatchers/courses.py`: 1,411 lines.
  - `studio/views/sync.py`: 1,249 lines.
  - `integrations/services/github_sync/orchestration.py`: 883 lines.
- Largest templates (refreshed 2026-07-21):
  - `templates/studio/base.html`: 1,218 lines.
  - `templates/accounts/account.html`: 1,138 lines.
  - `templates/studio/users/detail.html`: 1,074 lines.
  - `templates/base.html`: 809 lines.
- Largest JavaScript file:
  - `static/js/studio/plan_editor.js`: 1,052 lines.

## Highest-Risk Hotspots

### 1. GitHub content sync is doing too many jobs

Files:

- `integrations/services/github_sync/orchestration.py`
- `integrations/services/github_sync/dispatchers/courses.py`
- `integrations/services/github_sync/dispatchers/workshops.py`
- `integrations/services/github_sync/dispatchers/events.py`

Evidence:

- The repo-sync orchestration function handles locking, HEAD skip logic, queue log adoption, repo clone/pull, file count guards, S3 upload, tier sync, classification, dispatch, source status mutation, failure handling, temp cleanup, and follow-up queueing in one place.
- File classification encodes repository ownership rules, routing priority, YAML/Markdown parsing fallback behavior, malformed file handling, and path conventions.
- Course sync mixes YAML validation, slug collision handling, README fallback parsing, access-level parsing, Course upsert, orphan FK reattachment, instructor M2M handling, and module sync.
- The unit-sync path is one of the largest functions in the tree.

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

- The scan found about 214 `except Exception` handlers outside migrations and generated/static directories (2026-07-21 refresh).
- Concentrated areas include:
  - `integrations/services/github_sync/*`
  - `payments/services/*`
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
3. Add a lint rule such as Ruff `BLE001` once intentional cases are annotated (now staged in `ruff-advisory.toml`; see `_docs/lint.md`).

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

### 6. The plan editor JavaScript is a full client application in one file

File:

- `static/js/studio/plan_editor.js`

Evidence:

- 1,052 lines in a single IIFE (2026-07-21 refresh).
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

- `templates/studio/base.html`
- `templates/accounts/account.html`
- `templates/studio/users/detail.html`
- `templates/base.html`

Evidence:

- Multiple templates exceed 800 lines (2026-07-21 refresh).
- `templates/base.html` has 12 script tags.
- Some reusable admin widgets still carry many inline style attributes.

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

- Many status-code-only assertions and many `assertContains(..., "literal")` style assertions.
- Heavy use of mock/patch.
- Several test files are extremely large (e.g. `integrations/tests/test_github_sync.py`, `integrations/tests/test_workshop_sync.py`, `events/tests/test_events.py`).

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

See `_docs/audits/2026-05-12-test-suite-audit.md` for the dedicated test-quality and runtime-optimization plan, including candidates to remove from default CI.

## Tooling Gaps

Current mandatory Ruff config is intentionally minimal (`F`, `I`, `PLC0415`).
The expanded checks (`B`, `BLE`, `C4`, `SIM`, `RET`, `ARG`, `T20`, `C901`) now
run in advisory mode via `ruff-advisory.toml`; see `_docs/lint.md` for the
staging and promotion policy.

## Priority Refactor Plan

1. Characterize GitHub sync behavior with focused tests, then split orchestration and dispatch responsibilities.
2. Move Studio user filtering toward QuerySet/database-level filtering.
3. Split `plan_editor.js` around API, state transitions, and DOM wiring.
4. Decompose the largest templates after the view/service boundaries are clearer.

## What Looks Suspicious But Not Urgent

- The broad test count indicates active coverage work, but the shape of the tests suggests the suite may be expensive to maintain and may still miss behavior regressions in the most complex services.
