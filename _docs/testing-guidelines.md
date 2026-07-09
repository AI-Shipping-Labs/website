# Testing Guidelines

Rules for writing tests in this codebase. Read this before writing or reviewing any test.

---

## Rule 1: Every assertion must fail if the feature is broken

A test that passes regardless of whether the feature works is worse than no test --
it provides false confidence.

Bad -- assertion always passes because "2" appears somewhere on any HTML page:
```python
body = page.content()
assert "2" in body  # supposed to check "2 of 3 completed"
```

Bad -- conditional guard silently skips the assertion:
```python
if form.is_valid():
    self.assertEqual(form.cleaned_data["field"], expected)
# if form is invalid, the test passes with zero assertions
```

Bad -- asserts the same state the object already had:
```python
project = Project.objects.create(status="pending_review", published=False)
response = self.client.post(f"/studio/projects/{project.id}/reject/")
project.refresh_from_db()
self.assertEqual(project.status, "pending_review")  # it was already pending_review
self.assertFalse(project.published)                  # it was already False
```

Good -- assert the specific expected change:
```python
self.assertTrue(form.is_valid(), f"Form errors: {form.errors}")
self.assertEqual(form.cleaned_data["field"], expected)
```

Good -- use `assertNotContains` alongside `assertContains` for filter tests:
```python
response = self.client.get("/studio/campaigns/?status=draft")
self.assertContains(response, "Draft Campaign")
self.assertNotContains(response, "Sent Campaign")  # verify filtering works
```

---

## Rule 2: Assert on specific elements, not full HTML body

`assertIn("Free", response.content.decode())` matches any occurrence of "Free" on the
page -- nav items, CSS classes, JavaScript variables, alt text.

Bad:
```python
content = response.content.decode()
self.assertIn("Free", content)
self.assertIn("disabled", content)
self.assertIn("Subscribe", content)
```

Good -- use Django's `assertContains` with `html=True` for HTML fragments:
```python
self.assertContains(response, '<span class="tier-badge">Free</span>', html=True)
```

Good -- check view context instead of rendered HTML:
```python
self.assertEqual(response.context["user_tier"].name, "Free")
```

Good -- in Playwright, use locator-scoped assertions:
```python
expect(page.locator(".tier-badge")).to_have_text("Free")
expect(page.locator('[data-testid="progress"]')).to_contain_text("2 of 3")
```

---

## Rule 3: Do not test Django framework behavior

Django's ORM, CASCADE deletes, field defaults, and admin class attributes are tested
by the Django project itself. Do not write tests for them.

Concretely, do not write tests for any of these:

- `Meta.ordering` round-trips (create two rows, list, assert order).
- `unique=True` constraints (create two rows with the same value, expect IntegrityError).
- `null=True` / `blank=True` field nullability.
- Field defaults (`BooleanField(default=False)`, `CharField(default='')`, etc.).
- `BaseUserManager.create_user` / `create_superuser` / email normalisation
  semantics — Django ships its own tests for those.
- `JSONField` storage round-trips (write a list, read it back).
- `date.strftime` formatting via `formatted_date` / `short_date` style helpers.
- `f'/blog/{slug}'`-style `get_absolute_url` formatters whose body is a single
  f-string with no logic.
- ORM `CASCADE` delete behaviour. The `on_delete` keyword is Django's; only
  `PROTECT` and `SET_NULL` (non-default) are worth covering.

Bad -- tests that a `BooleanField(default=False)` returns `False`:
```python
def test_email_verified_default_false(self):
    user = User.objects.create_user(email="a@b.com", password="pw")
    self.assertFalse(user.email_verified)  # tests Django, not your code
```

Bad -- tests that CASCADE delete works:
```python
def test_cascade_delete(self):
    self.course.delete()
    self.assertEqual(Module.objects.count(), 0)  # ForeignKey(on_delete=CASCADE) is Django default
```

Bad -- tests that saving a CharField and reading it back works:
```python
def test_can_set_stripe_customer_id(self):
    self.user.stripe_customer_id = "cus_123"
    self.user.save()
    self.user.refresh_from_db()
    self.assertEqual(self.user.stripe_customer_id, "cus_123")  # tests Django ORM
```

Bad -- tests static admin class attributes:
```python
def test_list_display_includes_columns(self):
    self.assertIn("email", CourseAdmin.list_display)
    self.assertIn("status", CourseAdmin.list_display)
```

When to test model behavior: test custom `save()` logic, computed properties,
`clean()` validation, custom managers/querysets, and non-trivial `__str__`. Only
test `on_delete` if you use `PROTECT` or `SET_NULL` (non-default behavior).

---

## Rule 4: Do not test JavaScript by string-matching HTML

Checking that a JavaScript snippet exists in the rendered HTML tests that the
template includes the string, not that the code works.

Bad:
```python
content = response.content.decode()
self.assertIn("checkbox.checked = false", content)
self.assertIn("localStorage.getItem('theme')", content)
self.assertIn("function updateCancelButton()", content)
```

These are anti-refactoring anchors: renaming a variable or reformatting the JS
breaks the test with zero functional regression. If JS behavior matters, write a
Playwright E2E test that clicks the element and asserts on the DOM change.

---

## Rule 5: Do not test URL resolution separately

If your view tests call `self.client.get("/blog/")` and assert on the response,
the URL resolution is already tested. A separate `URLResolutionTest` that calls
`reverse()` and `resolve()` on every URL adds no value.

---

## Rule 6: Do not test constants or config dictionaries

Bad:
```python
def test_poll_type_level_map(self):
    self.assertEqual(POLL_TYPE_LEVEL_MAP["topic"], 20)
```

The behavior this constant drives (auto-setting `required_level` on save) should
be tested. The constant itself should not.

---

## Rule 7: Use `setUpTestData` for read-only fixtures

`setUp` runs before every test method. `setUpTestData` runs once per class and
wraps each test in a transaction rollback. For read-only data (tiers, content
fixtures), always use `setUpTestData`.

```python
class MyTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tier = Tier.objects.create(name="Free", slug="free", level=0)
        cls.user = User.objects.create_user(email="a@b.com", password="pw")

    def test_something(self):
        # self.tier and self.user are available, created once
        ...
```

---

## Rule 8: Shared fixtures go in shared modules

Do not copy-paste setup code across test files. Use:
- `tests/fixtures.py` for project-wide mixins (like `TierSetupMixin`)
- `playwright_tests/conftest.py` for shared Playwright helpers

---

## Rule 9: No hardcoded waits in Playwright tests

Bad:
```python
page.wait_for_timeout(2000)
```

Good:
```python
page.locator(".completion-badge").wait_for(state="visible")
expect(page.locator(".status")).to_have_text("Completed")
```

Use `wait_until="domcontentloaded"` instead of `wait_until="networkidle"` for
`page.goto()`, then wait for the specific element you need.

---

## Rule 10: E2E tests test user flows, not implementation details

Playwright tests should verify what a user sees and does. They should not:
- Call `call_command()` and assert on ORM objects (that's a Django TestCase)
- Use `unittest.mock.patch` to mock services (that's a unit test)
- Parse YAML config files (that's a standalone pytest)

If a test never opens a browser, it does not belong in `playwright_tests/`.

---

## Rule 11: One authoritative test per behavior

Do not test the same behavior in multiple places. Pick the right layer:

| Behavior | Test layer |
|---|---|
| Model logic, computed properties | Django `TestCase` |
| View responses, API contracts | Django `TestCase` |
| Access control matrix | Django `TestCase` |
| External service integration | Django `TestCase` with mocks |
| User-facing flows (login, purchase, navigation) | Playwright E2E |
| JavaScript interactions (modals, toggles, forms) | Playwright E2E |

Cross-layer dedup example: issue #261 removed Django per-content-type
detail-view tier matrices (article / recording / project / tutorial)
once `playwright_tests/test_access_control.py` covered each scenario
end-to-end. The Django layer kept the access function unit tests
(`CanAccessTest`, `BuildGatingContextTest`) and one smoke test per
detail view; everything else moved to Playwright as the single
authoritative source.

---

## Rule 12: Verify side effects of unauthorized requests

When testing that an endpoint rejects unauthorized access, also verify that
no side effects occurred in the database.

Bad:
```python
def test_vote_requires_authentication(self):
    response = self.client.post("/api/vote/", ...)
    self.assertEqual(response.status_code, 401)
```

Good:
```python
def test_vote_requires_authentication(self):
    vote_count_before = PollVote.objects.count()
    response = self.client.post("/api/vote/", ...)
    self.assertEqual(response.status_code, 401)
    self.assertEqual(PollVote.objects.count(), vote_count_before)
```

---

## Test databases, seed data, and local content

The repository's normal local database is `db.sqlite3`. It is for local
development, manual browsing, `seed_data`, and content synced from configured
content repositories. It is not a scratchpad for Playwright or QA fixtures.

`uv run pytest ...` uses pytest-django to create a separate test database.
`uv run pytest playwright_tests/...` starts the Django server from
`playwright_tests/conftest.py` after pytest has switched Django to that test
database. The server fixture has an unsafe database guard and will fail before
migrations or fixture helpers run if it is pointed at `db.sqlite3` or another
database name that is not test-scoped.

Do not import Playwright fixture helpers into a Django shell or run them against
`db.sqlite3`; that writes browser-test courses, workshops, users, and articles
into your development database. Run the Playwright test through pytest instead:

```bash
uv run pytest playwright_tests/test_some_flow.py -v
```

Use `uv run python manage.py seed_data` when you intentionally want local
development sample users, tiers, polls, and content. Use `sync_content` for
content repository data, which carries `source_repo` and `content_id` metadata.
Synced rows are source-owned; do not clean them up as test fixtures.

If a local `db.sqlite3` already contains obvious unsynced QA/test content rows,
preview cleanup first:

```bash
uv run python manage.py cleanup_qa_fixtures
```

The command lists likely unsynced fixture rows and deletes nothing by default.
After reviewing the list, apply the cleanup explicitly:

```bash
uv run python manage.py cleanup_qa_fixtures --apply
```

The cleanup command only considers rows with known QA/test signatures and
protects content rows that have `source_repo` or `content_id` set.

---

## Rule 13: Freeze time for time-dependent tests

Use `freezegun` or `time_machine` for boundary, status-transition, reminder,
join-window, countdown, or exact date-copy assertions. Tests that compare
against wall-clock time are race-condition prone on slow CI.

Relative dates such as `timezone.now() + timedelta(days=7)` are acceptable
when the test only needs data safely in the future or past and does not assert
an exact boundary, countdown, date-derived status, or hardcoded date label.
When fixture dates are generated dynamically, compute expected labels from
those same fixture dates.

For Playwright tests, do not seed future/current-sensitive fixtures with
near-current fixed dates such as `datetime(2026, ...)` or `"2026-06-01"`.
Events, cohorts, sprints, dashboards, registrations, Studio lists/edit forms,
join windows, and active/upcoming/default-list-visible scenarios must use
`timezone.now()` / `timezone.localdate()` plus `timedelta(...)`, or run under
an explicit frozen clock. Exact date-copy assertions should either freeze time
or derive the expected label from the generated fixture date.

Intentional fixed dates are allowed only when the historical/canonical/frozen
intent is visible in code. Use a same-line or preceding-line comment such as
`# date-rot-ok: canonical legacy workshop URL`, or a named helper/constant such
as `fixed_start`, `historical_event_date`, or `FIXED_WORKSHOP_CATALOG_DATE`.
The Playwright static guard in `playwright_tests/test_date_rot_guard.py` fails
unreasoned hard-coded 2026 dates in future-sensitive event/cohort/sprint fields
and date form inputs with file/line remediation guidance.

Bad:
```python
event = Event.objects.create(start=timezone.now() + timedelta(hours=24))
check_event_reminders()  # might drift outside the window on slow CI
```

Good:
```python
from freezegun import freeze_time

@freeze_time("2026-03-21 12:00:00")
def test_24h_reminder(self):
    event = Event.objects.create(start=datetime(2026, 3, 22, 12, 0, tzinfo=utc))
    check_event_reminders()
    self.assertEqual(Notification.objects.count(), 1)
```

---

## Rule 14: Verify email delivery with `mail.outbox`

Django's test runner uses an in-memory email backend. Use it.

```python
from django.core import mail

def test_registration_sends_verification_email(self):
    self.client.post("/api/register/", {"email": "new@example.com", "password": "pass1234"})
    self.assertEqual(len(mail.outbox), 1)
    self.assertIn("verify", mail.outbox[0].subject.lower())
    self.assertEqual(mail.outbox[0].to, ["new@example.com"])
```

---

## Rule 15: Choose the right test layer for the right job

Default to Django `TestCase` unless the test genuinely needs JavaScript or a real
browser. Browsers are roughly 100x slower than the Django test client, and every
Playwright test we add slows the whole suite for everyone.

### Use Playwright when

- The user interaction triggers JS that updates the DOM without a full reload
  (vote counter, mark-as-complete toggle, dropdown open, modal open/close,
  inline edit, autosave indicator).
- The test verifies an AJAX call's effect on the page (form submit that updates
  inline, not a redirect).
- The test depends on a browser dialog (`alert`, `confirm`, `prompt`).
- The test depends on browser-only behaviour (clipboard copy, `localStorage`,
  video player state, scroll position, keyboard shortcuts).
- The flow spans multiple pages with intermediate JS state that has to survive
  a navigation (e.g. wizard with client-side form draft).
- The test verifies an auto-refresh, polling, or `setInterval`-driven update.

### Use Django `TestCase` when

- The page is fully server-rendered and the test asserts on the HTML.
- The test asserts on response status, headers, redirects, or `response.context`.
- The interaction is a regular form POST that returns a redirect or a re-render
  (no JS in the loop).
- Filtering, sorting, or pagination is server-side via querystring.
- The test asserts on side effects (DB rows created, emails sent via
  `mail.outbox`, files written, async tasks enqueued).
- The test covers a model invariant, computed property, or custom `clean()`.
- The test covers an API contract (status code, JSON shape, error payload).
- The test covers an access-control or permission gate (anonymous vs free vs
  paid tier returning the right status / template / context).

### Heuristic

If you can write the test as `self.client.get(url)` followed by
`assertContains(response, ...)` (or `response.context[...]`) without losing
meaningful coverage, it belongs in Django. Reach for Playwright only when there
is a concrete JS or browser behaviour that the test client cannot reproduce.

A second heuristic: open the template the test exercises. If the behaviour you
are asserting on is produced by Django rendering server-side, test it server-side.
If it is produced by a `<script>` block or an `hx-*` / `data-*` attribute that
runs in the browser, test it in Playwright.

### Cross-reference

- Rule 10 (E2E tests test user flows, not implementation details) — Playwright
  must actually open a browser. Don't use it as a slow Django runner.
- Rule 11 (one authoritative test per behaviour) — pick a single layer per
  behaviour and stick to it. The matrix in Rule 11 is the canonical mapping.

### Canonical examples (workstream 3 of #170)

The cleanup tracked under #254 (sub-issues #255-#264) is the worked example for
this rule. It moved ~95 assertions from Playwright to Django and removed ~53
redundant Django tests, in both directions:

- Server-rendered HTML, status codes, context, framework behaviour, URL
  resolution, config constants, template strings, impossible edge cases, and
  cross-layer duplicates moved out of Playwright (or were deleted from Django
  where Playwright was already authoritative). See #255, #258, #261.
- Real JS interactions (mark-complete toggle, vote button, modal close,
  dropdowns, auto-refresh) stayed in Playwright but were simplified to assert
  on the specific DOM change rather than re-checking server-rendered text. See
  #256, #257, #259, #260, #262, #263, #264.

Use these issues as the reference when you are unsure which layer a new test
belongs in. If your test would have been deleted or moved by one of those
sub-issues, write it on the other layer to begin with.

---

## Rule 16: Don't test framework defaults

Restating Rule 3 in checklist form because the audit under issue #533 found
~80 tests still slipping through. Skip:

- `Meta.ordering` round-trips.
- `unique=True` IntegrityError checks.
- `null=True` / `blank=True` field nullability.
- `BaseUserManager` semantics (`create_user`, `create_superuser`,
  `normalize_email`).
- `JSONField` storage round-trips.
- `date.strftime` formatting wrappers (`formatted_date`, `short_date`).
- `f'{slug}'` / `f'/blog/{slug}'` URL-only `get_absolute_url` formats.
- ORM `CASCADE` delete propagation.

The framework owns these. If a future Django release breaks any of them, our
tests aren't going to be the canary.

---

## Rule 17: Don't test trivial `__str__`

A `__str__` that's a single f-string (`f'{self.title}'`,
`f'{self.subject} ({self.status})'`) is just attribute formatting. Skip the
test.

The exception is branching / conditional `__str__` formats. Those are worth
exactly one ``subTest``-parameterized test that exercises every branch:

```python
def test_str_branches_on_completed_at(self):
    cases = [
        ('completed', timezone.now()),
        ('in progress', None),
    ]
    for expected_marker, completed_at in cases:
        with self.subTest(expected_marker=expected_marker):
            progress = UserCourseProgress(
                user=self.user, unit=self.unit, completed_at=completed_at,
            )
            self.assertIn(expected_marker, str(progress))
```

---

## Rule 18: Parameterize per-enum tests

If you find yourself writing one test per branch of a `match` / dict lookup
(`test_difficulty_color_beginner`, `test_difficulty_color_intermediate`,
`test_difficulty_color_advanced`, …), collapse them into a single
``subTest``-parameterized table:

Bad:
```python
def test_difficulty_color_beginner(self):
    self.project.difficulty = 'beginner'
    self.assertEqual(self.project.difficulty_color(), 'bg-green-500/20 text-green-400')

def test_difficulty_color_intermediate(self):
    self.project.difficulty = 'intermediate'
    self.assertEqual(self.project.difficulty_color(), 'bg-yellow-500/20 text-yellow-400')

# … one method per row …
```

Good:
```python
def test_difficulty_color_table(self):
    cases = [
        ('beginner', 'bg-green-500/20 text-green-400'),
        ('intermediate', 'bg-yellow-500/20 text-yellow-400'),
        ('advanced', 'bg-red-500/20 text-red-400'),
        ('', 'bg-secondary text-muted-foreground'),
    ]
    project = Project(slug='difficulty-test')
    for difficulty, expected_class in cases:
        with self.subTest(difficulty=difficulty):
            project.difficulty = difficulty
            self.assertEqual(project.difficulty_color(), expected_class)
```

Adding a new branch is one row in the table, not one new test method. Each
row still fails on its own (the `subTest` context manager prints the failing
key). Truth tables (`is_closed_truth_table` for `Poll.is_closed`) follow the
same pattern.

---

## Rule 19: Bare `status_code == 200` is not a test

A smoke test that only asserts `self.assertEqual(response.status_code, 200)`
verifies the URL routes — nothing else. The view could return a blank
template, the wrong template, or a server error rendered as 200, and the
test still passes.

Bad:
```python
def test_dashboard_loads(self):
    response = self.client.get('/dashboard/')
    self.assertEqual(response.status_code, 200)
```

Good — follow the status check with at least one content/structural assertion:

```python
def test_dashboard_loads(self):
    response = self.client.get('/dashboard/')
    self.assertEqual(response.status_code, 200)
    self.assertTemplateUsed(response, 'dashboard/dashboard.html')
    self.assertContains(response, 'data-testid="dashboard-recent-content"')
```

The status code check stays — a 500 or redirect should still fail loudly —
but it never stands alone.

---

## Rule 20: Mock assertions verify behaviour, not coverage

`mock.assert_called_once()` proves the function was called. It does NOT prove
it was called correctly. A test that mocks `send_email`, calls the view, and
then asserts `mock_send.assert_called_once()` passes even if the mock was
called with the wrong recipient, the wrong template, or empty context.

Bad — mock theatre:
```python
@patch('events.services.send_registration_confirmation')
def test_registration_sends_confirmation(self, mock_send):
    self.client.post('/api/events/foo/register', {'email': 'a@b.com'})
    mock_send.assert_called_once()  # passes for any call shape
```

Good — pin the arg shape that matters:
```python
@patch('events.services.send_registration_confirmation')
def test_registration_sends_confirmation(self, mock_send):
    self.client.post('/api/events/foo/register', {'email': 'a@b.com'})
    mock_send.assert_called_once()
    args, kwargs = mock_send.call_args
    self.assertEqual(kwargs['user'].email, 'a@b.com')
    self.assertEqual(kwargs['event'].slug, 'foo')
```

Better — exercise the actual outcome instead of the mock. If the side effect
is observable (`mail.outbox`, a DB row, a notification record), assert on
that and skip the mock entirely:

```python
def test_registration_sends_confirmation(self):
    self.client.post('/api/events/foo/register', {'email': 'a@b.com'})
    self.assertEqual(len(mail.outbox), 1)
    self.assertEqual(mail.outbox[0].to, ['a@b.com'])
    self.assertIn('foo', mail.outbox[0].subject)
```

Use mocks when the real call would hit a network (Stripe, Slack, SES API,
GitHub). Even then, pin the arg shape that proves the call was correct.

---

## Coverage gate (`make coverage`)

`make coverage` is the exhaustive Django coverage gate. It starts from clean
coverage data, runs the full Django unit/integration suite with Coverage.py,
and reports against the project coverage configuration in `pyproject.toml`.
The command must pass with at least 85% total coverage in CI.

For per-issue local review, do not run `make coverage` by default. It is
CI-only unless Alexey explicitly asks for a local full-suite/coverage run. Local
tester review should run focused Django tests for the changed modules plus the
appropriate Playwright subset.

The coverage scope is first-party runtime/application code: Django apps plus the
project package. Django test modules, Playwright test modules, migrations,
virtualenvs, cache/build output, and local generated artifacts are excluded from
the percentage. Do not exclude runtime code solely to raise the reported total.

Playwright E2E tests are a separate validation gate:

```bash
make coverage      # CI/default exhaustive Django/runtime coverage, 85% minimum
make playwright    # browser E2E scenarios
```

Playwright source files are not counted as uncovered Django coverage debt unless
a future issue intentionally adds combined E2E coverage collection and combines
the data deliberately.

---

## Core test subset (`make test-core`)

The full Django suite has thousands of tests and takes 1-3 minutes. For the
inner loop (TDD, quick sanity checks before pushing) we maintain a tagged
subset that runs in well under a minute.

```bash
make test-core          # ~800 tests in ~30s, parallel
make test               # full suite, parallel
```

`make test-core` runs `python manage.py test --tag=core --parallel`. CI
continues to run the full suite -- the tag is a local-development convenience,
not a substitute for `make test` before merging.

### What belongs in `core`

A test class should be tagged `@tag('core')` if it covers any of:

- Authentication flows (login, signup, email verification, password reset).
- Tier-based access control matrix (free/basic/main/premium gating on every
  content type).
- Course purchase + Stripe webhook handlers (checkout, subscription updated,
  subscription deleted, invoice failed, idempotency).
- Sync upsert correctness for each content type -- one happy path per content
  type, not exhaustive edge cases.
- Critical model invariants (Course.required_level, User.tier, Enrollment
  uniqueness, TierOverride lifecycle, CourseAccess gating).
- Studio access gates (staff-only and superuser-only views).
- Notification creation + delivery, vote submission, newsletter subscribe
  happy paths.

### What does not belong in `core`

- Migration data-backfill tests.
- Slow integration tests with image-upload mocks or external SDK calls.
- One-off edge cases and defensive guards.
- Admin UI rendering / `list_display` / `list_filter` assertions.
- Mobile-specific responsive smoke tests.
- Anything that exercises Django framework behaviour itself (see Rule 3).

### How to tag

Apply the tag at the class level (most ergonomic):

```python
from django.test import TestCase, tag

@tag('core')
class CourseAccessControlTest(TestCase):
    ...
```

Per-method tagging works too, but prefer one decorator per `TestCase` so the
selection is auditable at a glance.

If you add a new feature to a critical path, tag the test class. If you remove
one, the tag travels with the deletion. There is no separate registry to
maintain -- the tag IS the registry.

---

## Core Playwright subset (`make test-playwright-core`)

The full Playwright suite has 1000+ tests across 150+ files and takes too long
to block every deploy. To get real Playwright coverage on every push to `main`
(Deploy Dev) without blowing the deploy budget, we maintain a small `core`
subset focused on deploy-critical user and operator journeys.

```bash
make test-playwright-core    # ~100-150 tests, target <8 min local / <15 min CI
make test-playwright         # full suite, runs on schedule (every 3h)
make test-playwright-manual-visual
```

`make test-playwright-core` runs `pytest -m core playwright_tests/ -v`. The
Deploy Dev workflow runs the same command in a parallel `playwright-core`
job with the default marker exclusion:

```bash
pytest -m "core and not manual_visual and not slow_platform" playwright_tests/ -v
```

A failure blocks the deploy. The scheduled workflow runs the broader Playwright
suite every 3 hours, skipped if no commits have landed since the last successful
run. That scheduled default is sharded across separate GitHub Actions matrix
jobs and uses:

```bash
pytest -m "not manual_visual and not slow_platform" <shard files> -v
```

Manual dispatch of `scheduled-playwright.yml` can also run the excluded marker
suites by enabling the `include_excluded` input. Run
`make test-playwright-manual-visual` locally when you specifically need the
screenshot/manual-review suites.

### Special Playwright and platform markers

Use these markers to keep default CI focused while preserving opt-in coverage:

| Marker | Use for | Default CI behavior |
|---|---|---|
| `core` | Deploy-critical user/operator smoke paths (auth, access control, payments, one happy path per major content type, Studio safety). | Run on every push via Deploy Dev's `playwright-core` job (`make test-playwright-core`). Included in the scheduled full suite. |
| `manual_visual` | Screenshot generators and tests whose primary output is manual visual review. | Excluded from Deploy Dev and the scheduled default; runnable with `make test-playwright-manual-visual` or scheduled manual dispatch with `include_excluded`. |
| `slow_platform` | SQLite/threading/migration/concurrency tests or equivalent platform-level checks that are valuable but slow or contention-prone. | Excluded from default pytest-marker Playwright runs. For Django `manage.py test`, mirror with `@tag('slow_platform')` when practical so future tag-based runs can exclude it explicitly. |
| `visual_regression` | Automated CSS class / Tailwind utility / spacing / color / layout-density assertions (`px-5`, `min-h-*`, `bg-card`, `flex-col`, `max-w-7xl`, etc.). Distinct from `manual_visual`: these run unattended, they just shouldn't gate push. | Excluded from `make test`, `make test-core`, `make test-playwright`, `make test-playwright-core`, Deploy Dev, and `ci.yml`. Included in the scheduled Playwright workflow's default run. Run on demand with `make test-visual-regression`. Playwright tests use `@pytest.mark.visual_regression`; Django tests use `@tag('visual_regression')`. |
| `local_only` | Tests that require the local Django runner — direct ORM seeding, session-cookie injection, `create_user`/`create_staff_user`/`auth_context`/`create_session_for_user`/`ensure_tiers`/`ensure_site_config_tiers` helpers, or pytest-django DB fixtures. They cannot run against a deployed environment. | Included in every run that hits a local base URL (default `PLAYWRIGHT_BASE_URL` unset, or set to `127.0.0.1`/`localhost`). Excluded automatically from the dev-environment scheduled suite (`scheduled-playwright-dev.yml`) — `playwright_tests/conftest.py` skips them when `PLAYWRIGHT_BASE_URL` is non-local. |
| `creates_data` | Tests that POST to write endpoints (`/api/projects/submit`, `/accounts/register/`, `/api/newsletter/subscribe`, etc.) without using an account from our local test fixtures. These would leave real rows behind on a shared dev DB. | Same gating as `local_only`: included locally, excluded from the dev-environment scheduled suite. |

### Dev-environment scheduled suite policy

`scheduled-playwright-dev.yml` runs the Playwright suite against
`https://dev.aishippinglabs.com` every 3 hours (offset 30 minutes from the
local-runner schedule). It has no in-process Django server, no migrations,
and no port 8765. The runner only hits HTTP — so any test that depends on
local DB state cannot pass against dev.

If your test creates DB rows, relies on a session/DB fixture, or POSTs to
a write endpoint, mark it `local_only` or `creates_data` so the dev
scheduled suite does not pick it up. The dev suite's marker filter
(`not manual_visual and not slow_platform and
not visual_regression and not local_only and not creates_data`) leaves
the anonymous, read-only subset of the suite as the dev-suite payload.
Note: `playwright_tests/conftest.py` does NOT auto-skip tests carrying
the pytest-django `django_db` marker on non-local runs — that marker
only enables ORM access during the test and many anonymous tests carry
it defensively without ever issuing a query. Each test/file is
responsible for tagging itself `local_only` if it genuinely needs the
local test database.

New dev-suite tests live in `playwright_tests/test_dev_smoke_*.py`. Each
file owns one public surface area (homepage, pricing, blog listing,
courses listing, etc.) and asserts only on stable, structural elements
that depend on data already present in the dev environment (content-repo
sync + `tiers.yaml` seed). Do not add `django_db`, `local_only`,
`creates_data`, or any DB-writing helper to a `test_dev_smoke_*.py`
file; if you need ORM seeding, the test belongs in a regular
`test_*.py` file marked `local_only`.

Transient-500 retry wrapper (Issue #928): every `test_dev_smoke_*.py`
navigates via `goto_with_retry(page, url, ...)` from
`playwright_tests/conftest.py`, not raw `page.goto`. The dev environment
runs on ECS and can briefly serve a 5xx (500/502/503/504) while a rolling
deploy swaps tasks; a single transient blip used to fail the whole shard
and fire `[CI] Scheduled Playwright (dev) full suite failing` on a false
red. `goto_with_retry` does a bounded retry (`attempts=3`, constant
`backoff_seconds=2.0`, so at most two retries / ~4s of added wait worst
case) on a retryable result — `response is None` (navigation error) or
`status >= 500`. The retry decision is the pure, unit-tested
`_is_retryable_status(status)`. Invariants:

- A non-5xx status (200, 301/302, 401, 403, 404, ...) is NEVER retried —
  it returns on the first attempt so callers assert on it exactly as
  before. The unknown-route test (`expected_status=404`) still gets its
  404 on the first attempt.
- The happy path adds zero latency: a first-attempt 200 returns
  immediately with no backoff sleep.
- A persistent 5xx STILL FAILS the test. The helper never raises and never
  fabricates a 200; after exhausting `attempts` it returns the last (still
  5xx / `None`) response, so the existing `assert response.status == 200`
  produces the normal, readable failure. The retry absorbs a transient
  blip, it never masks a sustained outage.

The retry helper is exercised without a live dev environment by
`tests/test_dev_smoke_goto_retry.py` (Django `SimpleTestCase` tagged
`core`, backoff sleep stubbed), so the logic is covered by `make
test-core` and push CI. The attempt count and backoff are test-harness
tuning constants (function kwargs in `conftest.py`), not runtime product
settings, so they intentionally do NOT go through the `IntegrationSetting`
framework. The infra root cause (why dev 5xxes during a deploy at all)
is tracked separately in `ai-shipping-labs-infra` (ECS task count, ALB
deregistration delay, health-check grace period).

Failure-issue lineages are intentionally separate:

- `[CI] Scheduled Playwright full suite failing` — local-runner suite
  (`scheduled-playwright.yml`, #681 lineage).
- `[CI] Scheduled Playwright (dev) full suite failing` — dev-environment
  suite (`scheduled-playwright-dev.yml`).

This lets on-call distinguish a code regression from a dev-environment
integration break at a glance.

Policy for class / Tailwind / layout assertions (extends Rule 2, "Assert on
specific elements, not full HTML body"): if a test asserts a specific Tailwind
utility class, hex color, `min-h-*`, `py-*`, breakpoint utility, or layout
density token, it is a visual contract assertion, not a behavior assertion.
Mark such tests `@pytest.mark.visual_regression` (Playwright) or
`@tag('visual_regression')` (Django) so push/core CI is not gated on UI
density iteration, while the scheduled workflow still catches regressions.
Default new tests away from class-substring asserts and toward
`data-testid` + behavior assertions per Rule 2. `core` and `visual_regression`
are intentionally orthogonal: a `core` smoke path should not inspect Tailwind
classes, so in practice a test should not carry both markers.

When an agent touches a template or layout file with existing class-substring
assertions, re-tag (or rewrite) the impacted test class as part of that
change rather than running a one-shot mass migration. The reference pattern
is `content/tests/test_footer_responsive.py`, where the five Tailwind-asserting
classes carry `@tag('visual_regression')` and a small sibling class
(`FooterNewsletterFormMessageHooksTest`) holds the JS-dependent ID-hook
contracts so they keep running on push.

### What belongs in `core`

A test should be tagged `@pytest.mark.core` only when it covers one of the
deploy-blocking smoke paths:

- Login, registration/email verification, and password reset happy paths.
- Stripe checkout/pricing redirect smoke, including the billing toggle.
- Tier-based access-control smoke across anonymous/free/basic/main/premium
  and one render path per major content type: articles, recordings/workshops,
  curated links/downloads, courses, and events.
- Course enrollment or unit-completion happy path.
- Event registration happy path.
- One sprint join/leave path and one member-plan/dashboard path.
- One notifications path.
- Minimal Studio staff safety coverage: access gating, one or two CRUD paths,
  and one content-source/sync smoke path.
- Public homepage/dashboard happy path.

Aim for roughly 100-150 tagged test functions. If a critical journey requires
exceeding 150, document why in the issue handoff. The criterion is "would we
block a deploy for this specific breakage?", not "does this feature matter
somewhere in the product?".

### What does not belong in `core`

Leave untagged (these run only on the scheduled job):

- Visual / typography / layout regression tests.
- Mobile-only / responsive smoke tests.
- Issue-specific cosmetic fixes (single-issue polish files like
  `test_clickable_cards_523.py`).
- Edge cases: empty states, malformed input, niche error pages.
- Broad Studio panel/sidebar/editor matrices, Studio polish/scanability files,
  plan-editor drag/drop variants, and admin-only edge cases.
- GitHub sync orchestration details beyond the minimal content-source/sync
  smoke path.
- Password-reset edge/error cases beyond the request/complete happy path.
- Broad event calendar/timezone/capacity matrices and event series variants.
- Broad account/billing-state modal matrices.
- Niche admin actions used rarely (contacts import, peer reviews, token
  revoke/list ownership, UTM import/archive variants).
- Theme toggle, env-mismatch banner, code copy widgets, foldable sidebar.
- Screenshot generators and manual visual review suites. Mark these with
  `@pytest.mark.manual_visual`, and keep at least one non-screenshot semantic
  visibility/no-overflow smoke in default CI for the affected surface when the
  manual suite was the only coverage.

### How to tag

Apply the marker at the function level (Playwright tests are mostly
function-style, so per-function is the norm):

```python
import pytest

@pytest.mark.core
def test_free_member_hits_paywall_on_basic_article(page, live_server, ...):
    ...
```

Class-level tagging works too if every method in the class belongs in core:

```python
@pytest.mark.core
class TestAccessControlMatrix:
    def test_anonymous_user_blocked(self, page, ...):
        ...
```

If you add a new feature on a critical path, tag the test. If you remove the
feature, the marker travels with the deletion. The marker IS the registry.

### Local concurrency guard

The old machine-wide "run one Playwright suite at a time locally" constraint is
resolved as of #885. See "Running Playwright in isolation / parallel across
worktrees" below: the server fixture now picks a free OS-assigned port per
session, so concurrent runs from separate worktrees no longer collide.

The remaining unsafe case is two local Playwright pytest sessions inside the
same git worktree, because they share that checkout's
`test_playwright_db.sqlite3`. This is now blocked by tooling. A direct
`uv run pytest playwright_tests/...` invocation or any Makefile Playwright
target claims `.tmp/playwright-session.lock` before migrations, Django
`runserver`, browser launch, or fixture seeding. A second local session in the
same worktree exits quickly with holder details and remediation instructions.

When `PLAYWRIGHT_BASE_URL` points at a non-local host such as
`https://dev.aishippinglabs.com`, the suite does not start the local server or
use the local SQLite test DB, so it does not claim the same-worktree guard.

## Running Playwright in isolation / parallel across worktrees

The Playwright server fixture in `playwright_tests/conftest.py` used to bind a
single hardcoded port (`8765`) for the in-process `runserver` thread. Both the
server bind and the URL the browser navigated to were derived from that one
constant, so two `pytest playwright_tests/` runs from different git worktrees
fought over the same port: the second run's `runserver` failed to bind `8765`
(address already in use) and the whole suite died. This forced all local E2E
runs to serialize.

As of #885 the local-server port is resolved once per pytest session:

- If `PLAYWRIGHT_DJANGO_PORT` is set and non-empty, that exact port is used.
- Otherwise the OS assigns a free ephemeral port (bind `127.0.0.1:0`, read the
  kernel-assigned port via `getsockname()`, close the probe socket, reuse it).

The resolved port is the value `runserver` binds, the value the startup probe
hits, and the value baked into the base URL the browser navigates to — they are
equal by construction, so the bound port can never drift from the navigated
port.

Git worktrees already isolate the code checkout and the SQLite test database
(each worktree's Playwright run uses its own `test_playwright_db.sqlite3`). With
the dynamic port, the last shared resource is gone: multiple agents in separate
worktrees can now run `make test-playwright` / `make test-playwright-core`
SIMULTANEOUSLY without interfering. Each agent verifies its own work
independently — there is no need to serialize on the port.

Do not start two local Playwright pytest sessions inside the same worktree. If
that happens, the second session fails before it can touch
`test_playwright_db.sqlite3`, showing the worktree path, current PID, holder
details when available, and instructions to wait, stop the other run, or use a
separate worktree. Normal pytest teardown releases the guard. If a pytest
process is killed, the underlying advisory lock is released by the OS, so stale
metadata in `.tmp/playwright-session.lock` does not permanently block the next
run.

```bash
make test-playwright          # full active suite
make test-playwright-core     # deploy-critical core subset
```

Run those from each worktree concurrently as needed; no port flags required.

### `PLAYWRIGHT_DJANGO_PORT` override

Set `PLAYWRIGHT_DJANGO_PORT` only when you want to pin a known port (e.g. for
debugging against a fixed URL, or attaching external tooling). When unset, the
OS-assigned free port is used — this is the normal path and what makes parallel
worktree runs safe. The remote/CI path is unaffected: when `PLAYWRIGHT_BASE_URL`
points at a remote host (e.g. `https://dev.aishippinglabs.com`) no local server
starts and no port is allocated at all.

### Supersedes the old constraint

This SUPERSEDES the previous rule "don't run two Playwright suites at once from
different worktrees." With per-session OS-assigned ports, concurrent
worktree runs are supported and expected.

## Live LLM-judge tests (`make test-judge`)

A SEPARATE, opt-in test set that exercises the two shipped AI callables --
`questionnaires.onboarding_ai.run_onboarding_turn` and
`integrations.services.feedback_synthesis.synthesize_feedback` -- against
the REAL configured LLM provider (currently Z.ai / glm via the
Anthropic-compatible gateway). Each scenario asserts a list of
plain-English criteria are true of the AI output, judged by an LLM, and
fails with the judge's own reasoning when a criterion is not met.

### How it differs from core and from the eval suite

- Core (`make test-core`, `make test-playwright-core`) is fully mocked,
  deterministic, and runs on every push. It makes zero live LLM calls.
- The eval suite (#812: `integrations/services/ai_eval/`, datasets +
  metrics) measures assistant quality over labeled datasets. It runs
  mocked by default and is about metrics, not per-scenario pass/fail.
- The live-judge set is neither: it is a small set of real-provider
  user-story scenarios, asserted pass/fail by an LLM judge, run on demand
  by a human with a key. It is NOT a quality metric and NOT part of CI.

### Location, marker, and CI isolation

- Tests live in `tests/live_judge/` -- a plain pytest package, NOT under
  `playwright_tests/` and NOT a Django app `tests/` module. CI runs only
  `manage.py test` (Django/unittest, which does not collect plain pytest
  functions) and `pytest playwright_tests/`, so neither leg ever collects
  this set.
- Every test carries the registered `live_judge` marker
  (`pytestmark = pytest.mark.live_judge`).
- The helper logic itself (the `assert_criteria` judge call and the cost
  tracker) is covered in CI by mocked unit tests in
  `integrations/tests/test_live_judge_helpers.py` -- those make no live
  calls and are not marked `live_judge`.

### How to run

```bash
# With a real provider configured (LLM_API_KEY set):
make test-judge        # -> uv run pytest -m live_judge tests/live_judge/ -n 4
```

Without a key, the whole set is SKIPPED (not errored): the conftest checks
`integrations.services.llm.is_enabled()` at collection time and skips every
test, so `make test-judge` on a no-key machine reports skips and makes zero
live calls. The make target is referenced by no CI workflow and by no other
make target (`test`, `test-core`, `test-all`).

At session end the cost tracker prints a per-model + total USD summary plus
the number of LLM calls and the percentage of criteria that passed. The
#799 `LLMResult` carries no token usage today, so the cost prints `$0.00`
until usage lands; the tracker is defensive and still prints the summary.

### The `LLM_JUDGE_MODEL` knob

The judge model is resolved from the `LLM_JUDGE_MODEL` config key, falling
back to `LLM_MODEL` when unset (so the default is judge == assistant model,
zero-config). Set `LLM_JUDGE_MODEL` to point the judge at a stronger or
cheaper model than the assistant under test without touching the
assistant's own `LLM_MODEL`.

### Logfire stays off

This set must not emit to Logfire. #813 owns the actual prod-only gating;
the live-judge conftest inherits it and additionally asserts that no
Logfire / OpenTelemetry span exporter is active during a run.
