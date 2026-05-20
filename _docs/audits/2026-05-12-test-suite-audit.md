# Test Suite Audit and Runtime Optimization - 2026-05-12

This audit focuses on test quality, maintainability, and runtime. It combines local inspection with parallel sub-agent reviews of Django tests, Playwright tests, payments/integrations tests, and pruning opportunities.

## Summary

The suite is broad, but the default run appears to include too much obsolete, visual/manual, and duplicated coverage. The fastest safe wins are:

1. Move screenshot/manual Playwright tests out of default CI.
2. Remove or quarantine tests for obsolete local Stripe Checkout/subscription APIs.
3. Stop forcing legacy Stripe Checkout mode in all Playwright tests.
4. Collapse duplicated Django vs Playwright coverage for pricing/account state.
5. Keep Playwright for real browser behavior and move server-rendered content checks back to Django tests.

## Current Runtime Shape

CI currently runs:

- Django tests with `manage.py test --parallel 4 --keepdb`.
- All Playwright tests serially via `uv run pytest playwright_tests/ -v`.

The Playwright command is the clearest wall-clock bottleneck because it is serial and includes screenshot/manual-review suites.

## Highest-Value Runtime Actions

### 1. Move screenshot/manual Playwright suites out of default CI

Estimated save: high, likely minutes. Risk: low if a small semantic smoke test remains.

Candidate suites:

- `playwright_tests/test_public_catalog_cards_544.py`: multi-route, multi-theme, multi-viewport screenshot capture.
- `playwright_tests/test_member_surfaces_543.py`: member/auth surfaces across themes and viewports with screenshots.
- `playwright_tests/test_project_cards_537.py`: explicitly described as screenshots for manual review.
- `playwright_tests/test_studio_peer_reviews_screenshots.py`: pure screenshot generator with hard waits.
- `playwright_tests/test_mermaid_theme_screenshots.py`: screenshot-only dark/light/toggle coverage.

Plan:

1. Add a `manual_visual` pytest marker.
2. Mark these files or tests.
3. Change default CI to run `pytest playwright_tests/ -m "not manual_visual"`.
4. Keep one semantic no-overflow/visibility assertion per affected surface in default CI.

### 2. Prune obsolete local Stripe Checkout/subscription tests

Estimated save: medium. Risk: medium unless done alongside stronger webhook/payment-link tests.

The current product model is Stripe Payment Links out, Stripe webhooks in, and Customer Portal for billing management. Local Checkout Session creation, upgrade, downgrade, cancel, and course-purchase session creation are no longer core behavior.

Prune or quarantine:

- `payments/tests/test_checkout.py`: tests `/api/checkout/create`, `/api/subscription/upgrade`, `/api/subscription/downgrade`, and `/api/subscription/cancel` with checkout enabled.
- `payments/tests/test_services.py`: `CreateCheckoutSessionTest`, `UpgradeSubscriptionTest`, `DowngradeSubscriptionTest`, and `CancelSubscriptionTest`.
- `payments/tests/test_checkout_feedback.py`: local Checkout Session success/cancel redirect behavior.
- `accounts/tests/test_account.py`: local upgrade/downgrade/cancel button tests.

Keep or strengthen:

- Checkout-disabled guard tests.
- Payment Link rendering tests.
- Webhook signature/idempotency/fulfillment tests.
- Customer Portal link presence tests.

Replacement tests to add:

1. Signed `checkout.session.completed` webhook while `STRIPE_CHECKOUT_ENABLED=False`, asserting user tier/customer/subscription updates and `WebhookEvent` creation.
2. Payment Link rendering for every paid tier and billing period from `settings.STRIPE_PAYMENT_LINKS`.
3. URL-encoded `prefilled_email`, including `+` and other special characters.
4. Customer Portal link is shown for paid/member states that need billing management.

### 3. Stop forcing legacy Stripe Checkout in Playwright

Estimated save: indirect but important. Risk: medium.

`playwright_tests/conftest.py` currently forces `settings.STRIPE_CHECKOUT_ENABLED = True` for the full browser session. That means browser tests exercise a non-default payment mode.

Plan:

1. Default Playwright to Payment Links mode.
2. Add a `legacy_checkout` marker only if one or two legacy smoke tests are intentionally kept.
3. Move `playwright_tests/test_pricing_account_state.py` and Stripe Checkout UI checks behind that marker or reduce them to payment-link/portal smoke coverage.

### 4. Collapse duplicated Django vs Playwright pricing/account state coverage

Estimated save: small-to-medium. Risk: low.

`payments/tests/test_pricing_account_plan.py` already covers anonymous, free, basic, main, premium, pending downgrade/cancel, override, and stale subscription states. Playwright repeats much of this in `playwright_tests/test_pricing_account_state.py`.

Plan:

1. Keep Django tests as canonical for tier-state matrix logic.
2. Keep at most one Playwright smoke for signed-in pricing card rendering.
3. Keep one account-page browser smoke for portal/payment-link availability.

### 5. Move server-rendered Playwright checks back to Django

Estimated save: modest individually, meaningful as a batch. Risk: low.

Examples:

- `playwright_tests/test_articles_blog.py` published-only listing overlaps `content/tests/test_blog.py`.
- Tag filtering Playwright coverage overlaps Django blog tag tests.
- Related articles Playwright coverage overlaps Django related-article tests.
- Admin create article Playwright coverage overlaps Django admin/article creation tests.

Plan:

1. Keep browser tests where JavaScript, browser navigation, auth cookies, or responsive layout are the behavior.
2. Move pure rendered HTML/list/filter checks to Django tests.

### 6. Mark slow platform tests for scheduled/nightly runs

Estimated save: low-to-medium. Risk: medium.

`tests/test_sqlite_concurrency.py` is valuable but uses threads, temporary SQLite files, and sleeps. Keep it, but consider a `slow_platform` marker if default CI remains too slow or flaky.

## Test Quality Findings

### Weak status-only tests

The Django/unit test audit found many tests where assertions are effectively only status/redirect checks. Examples include:

- `accounts/tests/test_email_auth.py`: many invalid-input cases assert only `400`.
- `content/tests/test_course_units.py`: examples that only assert `200`.
- `api/tests/test_course_certificates.py`: accepts URLs with status-only assertions instead of row/body semantics.

Remediation:

- For APIs, assert response schema, error code/message, DB side effects, no unintended side effects, auth identity, and idempotency.
- Parameterize invalid-input cases so richer assertions do not create hundreds of repetitive tests.

### Brittle HTML/CSS/JS string assertions

Examples:

- `content/tests/test_footer_responsive.py` asserts Tailwind class strings.
- `studio/tests/test_user_list_name_display.py` asserts spacing classes.
- `content/tests/test_header_account_menu.py` asserts exact JavaScript snippets.

Remediation:

- Reserve Django string assertions for semantic contracts.
- Use an HTML parser for forms, links, ARIA, and headings.
- Put real responsive/layout guarantees in focused browser tests.

### Tests that inspect implementation instead of behavior

Examples:

- `plans/tests/test_view_layer_no_visibility_literals.py` reads source and forbids strings in `plans/views/cohort.py`.
- `studio/tests/test_sync_dashboard.py` asserts exact `django_q.tasks.async_task` internals.

Remediation:

- Replace source-string tests with behavior tests.
- If architectural restrictions are needed, enforce them through lint/static checks outside the unit suite.

### Mock-heavy tests

Examples:

- `community/tests/test_services.py` patches `requests.post` but often asserts only high-level success.
- Recording upload tests stack Zoom/S3/request mocks repeatedly.
- `payments/tests/test_webhooks.py` uses broad Stripe subscription lookup mocks, hiding billing-period and period-end behavior.

Remediation:

- Use adapter contract tests with `responses`, `requests_mock`, or `botocore.stub.Stubber`.
- Keep task tests focused on DB state, audit logs, retry behavior, and no partial writes.
- Add one payment webhook endpoint test with mocked Stripe subscription retrieve response that asserts billing period, period end, attribution, and webhook status.

### Duplicated setup/factories

Repeated setup appears across course/module/unit tests, Studio user-list tests, sync tests, and Playwright auth/data setup.

Plan:

- Add lightweight helpers such as `make_user`, `make_course_with_units`, `make_content_source`, and `make_sprint_plan`.
- Move immutable graph setup to `setUpTestData` where possible.
- Use `MD5PasswordHasher` in auth-heavy tests unless password hashing is the behavior under test.

## Payments and Integrations Test Gaps

High-priority gaps:

1. Webhook fulfillment under the current disabled-checkout product mode.
2. Payment Link correctness for all tiers and billing periods.
3. URL-safe `prefilled_email`.
4. Stripe webhook endpoint using DB-backed `IntegrationSetting` secret, not just service-level signature verification.
5. GitHub webhook async enqueue path: task path, source argument, `force=True`, task name, queued `SyncLog`, and `source.last_sync_status == "queued"`.
6. GitHub webhook negative paths should assert no `SyncLog`, no `last_webhook_at`, no `sync_requested`, and expected webhook logging.

## Marker Policy Proposal

Add explicit markers:

- `manual_visual`: screenshot generators/manual review only.
- `legacy_checkout`: local Stripe Checkout/subscription API tests retained temporarily.
- `slow_platform`: SQLite/threading/migration/concurrency tests.
- `browser_smoke`: the default Playwright subset.

Default CI target:

```bash
pytest playwright_tests/ -m "not manual_visual and not legacy_checkout and not slow_platform"
```

Scheduled/nightly target:

```bash
pytest
```

For Django `manage.py test`, mirror this with Django tags where practical, or migrate tests that need pytest marker control into pytest-compatible suites.

## Playwright Parallelization

Playwright currently runs serially. Sharding by GitHub Actions matrix is safer than in-process xdist because `playwright_tests/conftest.py` uses a hardcoded port and DB file.

Plan:

1. First shard by file groups on separate CI runners.
2. Only consider `pytest-xdist` after per-worker ports and per-worker DB files are implemented.
3. Keep browser smoke tests small and deterministic before parallelizing; parallel flakes are harder to debug.

## Suggested First Pull Requests

1. Add pytest markers and exclude `manual_visual` from default Playwright CI.
2. Stop forcing `STRIPE_CHECKOUT_ENABLED=True` globally in Playwright; mark any retained legacy tests.
3. Delete or quarantine obsolete local Stripe checkout/subscription tests, keeping one disabled-guard smoke if needed.
4. Add current-model Stripe webhook + Payment Link tests.
5. Reduce `playwright_tests/test_pricing_account_state.py` to one or two browser smokes and rely on Django for the state matrix.
6. Move obvious server-rendered blog/list/filter Playwright checks to Django or delete duplicates when equivalent Django coverage already exists.

