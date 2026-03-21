# Test Remediation Plan

Step-by-step plan to clean up the test suite, remove useless tests, fix broken
tests, and increase meaningful coverage.

Audit date: 2026-03-21

## Current State

- 2,836 Django tests, running in 423 seconds (~7 minutes)
- ~30 Playwright E2E test files with 393 browser launches (additional ~15-20 min)
- Total CI wall time: ~25-30 min (unit tests run sequentially before E2E)
- ~150 tests identified as useless (test constants, Django ORM, admin config)
- 97 migration files applied per test DB creation

---

## Step 1: Delete tests that test nothing

These tests verify Django framework behavior, static constants, or config
attributes. They add no value and inflate the test count.

### 1a: Delete admin config attribute tests

Tests that assert on `list_display`, `list_filter`, `ordering`, `search_fields`,
`prepopulated_fields`, `inlines`, `fieldsets`, `readonly_fields`, `date_hierarchy`,
`actions`. These test static Python tuples, not behavior.

Delete these test classes entirely:
- `content/tests/test_course_admin.py` -- `CourseAdminConfigTest` (lines 28-99, ~13 tests)
- `content/tests/test_course_admin.py` -- `ModuleAdminConfigTest` (lines 102-121, 4 tests)
- `content/tests/test_course_admin.py` -- `UnitAdminConfigTest` (lines 123-158, 5 tests)
- `content/tests/test_course_admin.py` -- `UnitInlineConfigTest` (lines 160-183, 3 tests)
- `content/tests/test_course_admin.py` -- `ModuleInlineConfigTest` (lines 185-195, 2 tests)
- `voting/tests/test_admin.py` -- config tests (lines 27-43, 4 tests)
- `voting/tests/test_admin.py` -- `PollOptionAdminTest` (lines 102-110)
- `voting/tests/test_admin.py` -- `PollVoteAdminTest` (lines 113-121)
- `accounts/tests/test_admin.py` -- `test_admin_user_list_displays_columns` (lines 21-27)
- `content/tests/test_courses.py:883` -- `test_admin_slug_auto_generated` (duplicate)

Tests removed: ~35

### 1b: Delete bare smoke tests (status 200 only, no content assertions)

Delete these files entirely:
- `accounts/tests/test_admin.py` (5 tests, all bare 200 checks)
- `jobs/tests/test_admin.py` (4 tests, all bare 200 checks)

Delete individual tests:
- `content/tests/test_course_admin.py:212` -- `test_course_list_page_loads`
- `content/tests/test_course_admin.py:354` -- `test_module_list_page_loads`
- `content/tests/test_course_admin.py:722` -- `test_admin_cohort_add_page`

Tests removed: ~12

### 1c: Delete JavaScript string-matching tests

Delete these test classes entirely:
- `accounts/tests/test_cancel_confirmation.py` -- `CancelModalJavaScriptTest` (8 tests)
- `accounts/tests/test_theme.py` -- `ThemeBlockingScriptTest` (5 tests)
- `accounts/tests/test_theme.py` -- `ThemeCSSVariablesTest` (5 tests)
- `accounts/tests/test_theme.py` -- `ThemeProseStylesTest` (3 tests)
- `accounts/tests/test_theme.py` -- `InlineGradientMigrationTest` (4 tests)
- `accounts/tests/test_theme.py` -- `ThemeToggleFunctionalityScriptTest` (7 tests)
- `studio/tests/test_event_create_zoom.py` -- template JS tests (lines 283-339, ~6 tests)
- `studio/tests/test_recording_youtube.py` -- template JS tests (lines 286-363, ~6 tests)

Tests removed: ~44

### 1d: Delete Django ORM round-trip tests

These test that saving a CharField and reading it back works, or that
`BooleanField(default=False)` is `False`, or that CASCADE deletes cascade.

Delete from `accounts/tests/test_models.py`:
- `test_email_verified_default_false` (line 83)
- `test_unsubscribed_default_false` (line 87)
- `test_email_preferences_default_empty_dict` (line 91)
- `test_email_preferences_stores_json` (line 95)
- `test_stripe_customer_id_default_empty` (line 107)
- `test_subscription_id_default_empty` (line 112)
- `test_billing_period_end_default_null` (line 125)
- `test_pending_tier_default_null` (line 129)
- `test_can_set_tier_to_paid` (line 133)
- `test_can_set_pending_tier` (line 141)
- `test_can_set_stripe_customer_id` (line 149)
- `test_can_set_subscription_id` (line 157)
- `test_slack_user_id_default_empty` (line 167)
- `test_can_set_slack_user_id` (line 171)
- `test_user_has_all_spec_fields` (lines 179-200)

Delete from `accounts/tests/test_theme.py`:
- `test_field_exists_and_defaults_empty` (line 14)
- `test_can_set_dark` (line 19)
- `test_can_set_light` (line 26)
- `test_can_set_empty` (line 35)
- `test_max_length` (line 45)
- `test_blank_is_true` (line 49)

Delete from `accounts/tests/test_models.py`:
- `test_username_field_is_email` (line 64)
- `test_required_fields_is_empty` (line 67)
- `test_username_is_not_a_db_field` (line 74)

Delete CASCADE tests that test Django default behavior:
- `community/tests/test_models.py:72` -- `test_cascade_delete_user`
- `content/tests/test_courses.py:157` -- `test_cascade_delete` (ModuleModelTest)
- `content/tests/test_courses.py:235` -- `test_cascade_delete_from_module` (UnitModelTest)
- `content/tests/test_course_admin.py:799-840` -- entire `CascadeDeleteTest` class (4 tests)
- `content/tests/test_cohorts.py:169` -- `test_cascade_delete_from_course`
- `content/tests/test_cohorts.py:231,237` -- two enrollment cascade tests
- `voting/tests/test_models.py:175` -- `test_cascade_delete_with_poll`
- `voting/tests/test_models.py:226-239` -- three cascade tests in PollVoteModelTest
- `notifications/tests/test_models.py:75` -- `test_cascade_delete_user`
- `notifications/tests/test_models.py:30-40` -- three default value tests

Delete default field value tests from `notifications/tests/test_models.py`:
- `test_notification_body_default_empty` (line 30)
- `test_notification_url_default_empty` (line 34)
- `test_notification_read_default_false` (line 38)

Tests removed: ~40

### 1e: Delete URL resolution tests

- `content/tests/test_urls.py` -- entire file (13 tests)
  Every URL is already exercised by the view tests.

Tests removed: 13

### 1f: Delete constant/map tests

- `voting/tests/test_models.py:131` -- `test_poll_type_level_map`
  The behavior this drives is tested by `test_required_level_auto_set_on_save`.

Tests removed: 1

### 1g: Delete marketing copy assertions

- `content/tests/test_views.py:48-75` -- tests checking for "Turn AI ideas into",
  "Rolando", "AI Data Scientist" etc. These break on any copy change.

Tests removed: ~5

Total tests removed in Step 1: ~150

---

## Step 2: Fix false-positive tests

These tests pass even when the feature they name is broken. Fix each one.

| File | Test | Fix |
|---|---|---|
| `content/tests/test_video_player.py:489` | `test_admin_form_clean_timestamps_valid_json` | Replace `if form.is_valid():` with `assertTrue(form.is_valid())` |
| `content/tests/test_video_player.py:492` | `test_admin_form_clean_timestamps_empty` | Same fix |
| `content/tests/test_course_admin.py:745` | `test_admin_cohort_shows_enrollment_count` | Assert enrollment count string in response, or delete |
| `studio/tests/test_projects.py:109` | `test_reject_project` | Assert `status == 'rejected'` after action |
| `studio/tests/test_campaigns.py:36` | `test_list_filter_by_status` | Add `assertNotContains(response, 'Sent Campaign')` |
| `studio/tests/test_projects.py:38` | `test_list_filter_pending` | Add `assertNotContains` for published item |
| `email_app/tests/test_campaigns.py:544` | `test_draft_campaign_fields_editable` | Assert form fields lack `readonly` attribute |
| `email_app/tests/test_campaigns.py:549` | `test_sent_campaign_fields_readonly` | Assert form fields have `readonly` attribute |
| `email_app/tests/test_newsletter.py:419` | `test_subscriber_admin_filter_verified` | Add `assertNotContains` for unverified user |
| `email_app/tests/test_newsletter.py:434` | `test_subscriber_admin_filter_unsubscribed` | Add `assertNotContains` for active user |
| `accounts/tests/test_tier_override.py:1036` | `test_53_expires_at_midnight_utc` | Use `freezegun` to fix time |
| `content/tests/test_course_units.py:984` | Progress check | Replace `assert "1" in body` with locator assertion |

---

## Step 3: Extract shared fixtures

### 3a: Django test fixtures

Create `tests/fixtures.py`:

```python
from payments.models import Tier

class TierSetupMixin:
    @classmethod
    def setUpTestData(cls):
        cls.free_tier = Tier.objects.get_or_create(
            slug="free", defaults={"name": "Free", "level": 0})[0]
        cls.basic_tier = Tier.objects.get_or_create(
            slug="basic", defaults={"name": "Basic", "level": 10})[0]
        cls.main_tier = Tier.objects.get_or_create(
            slug="main", defaults={"name": "Main", "level": 20})[0]
        cls.premium_tier = Tier.objects.get_or_create(
            slug="premium", defaults={"name": "Premium", "level": 30})[0]
```

Update all 15+ files that duplicate this mixin to import from `tests.fixtures`.

### 3b: Playwright helpers

Move into `playwright_tests/conftest.py`:
- `_ensure_tiers()`
- `_create_user()`
- `_create_session_for_user()`
- `_auth_context()`
- `_create_staff_user()`

Update all 25+ Playwright files. This eliminates ~3,000 lines of duplication.

---

## Step 4: Remove duplicate tests across layers

Keep one authoritative test per behavior:

| Behavior | Keep | Remove from |
|---|---|---|
| Unit completion toggling | `content/tests/test_course_units.py` | `playwright_tests/test_course_catalog.py`, `playwright_tests/test_video_player.py` |
| Cancel button rendering | `accounts/tests/test_cancel_confirmation.py` | `accounts/tests/test_account.py` (duplicate assertions) |
| Admin publish/unpublish | `content/tests/test_course_admin.py` | `content/tests/test_courses.py` |
| Dashboard "Continue Learning" | `playwright_tests/test_dashboard.py` | `playwright_tests/test_course_catalog.py`, `playwright_tests/test_aihero_course.py` |
| Gating/paywall per content type | `content/tests/test_access_control.py` (unit), `playwright_tests/test_access_control.py` (E2E) | Gating-only scenarios in individual E2E files |

---

## Step 5: Move misplaced tests out of Playwright directory

These tests run with full Playwright infrastructure but never open a browser.
Move them to the appropriate Django test files to save CI time.

| Current location | Move to |
|---|---|
| `playwright_tests/test_ci_cd_pipeline.py` (entire file) | `tests/test_ci_config.py` |
| `playwright_tests/test_seed_data.py` (11 of 12 scenarios) | Merge into `content/tests/test_seed_data.py` |
| `playwright_tests/test_community_slack.py` (6 of 11 scenarios) | Merge into `community/tests/test_services.py` |
| `playwright_tests/test_email_campaigns.py` (1 scenario) | Merge into `email_app/tests/test_campaigns.py` |

---

## Step 6: Speed up the test suite

Current bottlenecks by the numbers:

| Metric | Value | Impact |
|---|---|---|
| `setUp` calls (runs per test method) | 319 across 76 files | Redundant DB writes every test |
| `setUpTestData` calls (runs once per class) | 24 across 16 files | Underused |
| Chromium launches (`sync_playwright()`) | 393 per full E2E run | ~1-2s overhead each = ~6-13 min wasted |
| `page.wait_for_timeout()` hardcoded waits | 30+ calls | Wastes seconds even when element is ready |
| Migration files | 97 | Applied per test DB creation |
| CI: Playwright waits for unit tests | `needs: unit-tests` | Sequential, not parallel |

### 6a: Convert `setUp` to `setUpTestData` where possible

319 `setUp` methods vs only 24 `setUpTestData`. Most `setUp` calls create
read-only fixtures (tiers, users, content) that are never mutated by tests.
Convert these to `setUpTestData` which runs once per class and wraps each
test in a transaction savepoint/rollback.

Audit each class: if tests only read from the fixtures (no `.save()`, no
`.delete()`, no field mutations on the setUp objects), convert to
`setUpTestData`. This alone can cut Django test time by 30-50%.

High-impact files (most setUp calls):
- `content/tests/test_tags.py` -- 12 setUp calls
- `content/tests/test_courses.py` -- 12 setUp calls
- `content/tests/test_access_control.py` -- 12 setUp calls
- `content/tests/test_seo.py` -- 12 setUp calls
- `integrations/tests/test_github_sync.py` -- 12 setUp calls

### 6b: Reuse browser instances in Playwright

Current state: 393 separate `sync_playwright()` + `chromium.launch()` calls.
Every test method starts and stops Chromium. At ~1-2s per launch, this wastes
6-13 minutes of pure browser startup overhead.

Fix: create a session-scoped browser fixture in `conftest.py`:
```python
@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        yield browser
        browser.close()

@pytest.fixture
def page(browser):
    context = browser.new_context(viewport={"width": 1280, "height": 720})
    page = context.new_page()
    yield page
    context.close()
```

Then update all test methods to accept `page` as a parameter instead of
managing their own `sync_playwright()` context. This reduces 393 browser
launches to 1.

### 6c: Replace hardcoded waits with auto-waiting

Every `page.wait_for_timeout(2000)` wastes 2 seconds even when the element
is ready in 50ms. Replace all 30+ occurrences with Playwright auto-waiting:

```python
# Before (always waits 2 seconds)
page.wait_for_timeout(2000)
assert "Completed" in page.content()

# After (waits only as long as needed, up to default timeout)
expect(page.locator(".status")).to_have_text("Completed")
```

Conservative estimate: 30 waits averaging 1.5s each = 45 seconds wasted per run.

### 6d: Replace `wait_until="networkidle"` with `"domcontentloaded"`

`networkidle` waits for zero network connections for 500ms -- slow and flaky.
Use `domcontentloaded` followed by waiting for a specific element.

### 6e: Run Django and Playwright tests in parallel in CI

Current CI config has `needs: unit-tests` on the Playwright job, making them
sequential. These are independent -- remove the dependency:

```yaml
# Before
playwright-tests:
  needs: unit-tests  # DELETE THIS LINE

# After
playwright-tests:
  # runs in parallel with unit-tests
```

This cuts total CI wall time by the duration of whichever job is shorter.

### 6f: Use `--parallel` for Django tests

Django's test runner supports `--parallel`:
```bash
uv run python manage.py test --parallel
```
This runs test classes across multiple processes. Add to CI:
```yaml
- name: Run unit and integration tests
  run: uv run python manage.py test --parallel
```

### 6g: Remove redundant `migrate` step in CI

Line 30 of `.github/workflows/ci.yml` runs `uv run python manage.py migrate`
before `uv run python manage.py test`. The test runner creates its own test
database and runs migrations automatically -- this step is wasted time. Delete it.

### 6h: Squash migrations

97 migration files are applied every time the test database is created.
Consider squashing migrations per app to reduce test DB setup time:
```bash
uv run python manage.py squashmigrations <app_name> <latest_migration>
```

### 6i: Prioritize the `content` app

The `content` app has 1,268 tests (45% of total) and takes 173 seconds (41%
of runtime). Converting its 12 heaviest `setUp` methods to `setUpTestData`
will have the single largest impact on total test time. Start here.

---

## Step 7: Strengthen weak assertions

Priority order (highest business impact first):

### 7a: Payment and access control tests

Replace `assertIn("Subscribe", content)` and similar with scoped assertions
or context checks in:
- `payments/tests/test_tier.py`
- `accounts/tests/test_account.py`
- `events/tests/test_events.py`

### 7b: Playwright E2E assertions

Replace `assert "word" in body` with locator assertions in:
- `playwright_tests/test_course_units.py` -- `"check-circle-2"`, `"1"` checks
- `playwright_tests/test_video_player.py` -- `"blur"` check
- `playwright_tests/test_project_showcase.py` -- `"202"` check
- `playwright_tests/test_community_slack.py` -- `href is not None` check

---

## Step 8: Fill coverage gaps

These are areas with zero test coverage that matter for production reliability.

### 8a: Email delivery verification

Add `django.core.mail.outbox` checks for:
- Registration verification email (`accounts/tests/test_email_auth.py`)
- Password reset email (`accounts/tests/test_email_auth.py`)
- Campaign send (`email_app/tests/test_campaigns.py`)
- Newsletter subscribe verification (`email_app/tests/test_newsletter.py`)

### 8b: Time-frozen tests

Add `freezegun` for:
- All scenarios in `notifications/tests/test_event_reminders.py`
- `accounts/tests/test_tier_override.py:1036`
- Any test comparing against `timezone.now()`

### 8c: Stripe error handling

Add to `payments/tests/test_services.py`:
- `stripe.error.CardError` during checkout
- `stripe.error.RateLimitError` during upgrade
- Network timeout during cancel
- Verify views return user-friendly errors, not 500s

### 8d: DB side-effect verification on auth failures

Add to API tests that check 401/403:
- Verify no `PollVote` created after unauthenticated vote attempt
- Verify no `PollOption` created after unauthenticated proposal attempt
- Verify no `CohortEnrollment` created after unauthorized enrollment attempt

### 8e: Tighten exception assertions

Replace `self.assertRaises(Exception)` with specific exception types:
- `studio/tests/test_redirects.py:37` -- use `IntegrityError`
- `integrations/tests/test_github_sync.py:100` -- use `IntegrityError`

---

## Step 9: Expand thin test files

These files have fewer than 5 tests and cover areas that need more:

| File | Current | Add |
|---|---|---|
| `notifications/tests/test_templatetags.py` | 3 tests | Test with 0 unread, broadcast notifications, performance with many notifications |
| `content/tests/test_context_processors.py` | 3 tests | Test missing/empty settings, multiple processors |
| `integrations/tests/test_models.py` | 3 tests | Test required fields, payload validation, ordering edge cases |
| `notifications/tests/test_slack.py` | Tests only `article` type | Add tests for event, course, recording, download, poll slack messages |

---

## Execution Order

Recommended order for implementing this plan:

1. Step 1 (delete useless tests) -- immediate, no risk
2. Step 2 (fix false positives) -- immediate, catches real bugs
3. Step 3 (extract fixtures) -- immediate, enables all future work
4. Step 6 (speed up) -- immediate impact on developer experience
5. Step 4 (remove duplicates) -- after fixtures are shared
6. Step 5 (move misplaced tests) -- after understanding which scenarios to keep
7. Step 7 (strengthen assertions) -- ongoing, prioritize by business impact
8. Step 8 (fill gaps) -- ongoing, add as part of feature work
9. Step 9 (expand thin files) -- lowest priority, add opportunistically
