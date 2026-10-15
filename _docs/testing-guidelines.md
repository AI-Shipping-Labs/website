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

## Rule 21: Django template constructs are single-line only

Django's lexer uses one non-DOTALL alternation, `({%.*?%}|{{.*?}}|{#.*?#})`, so
all three constructs are recognized only when the closing marker is on the same
line as the opening one. A construct that spans more than one line is not
lexed at all: its raw text renders as visible page content, or ships inside a
transactional email. This has leaked to production pages repeatedly, caught
only at design review.

A single-line `{# ... #}` is correct and stays fully supported — roughly 300
of them are in the tree. This is a rule about span, not a deprecation of the
construct.

The remedy differs by construct, which is why the failure message names the
one you hit. A comment has a block form to move to:
`{% comment %} ... {% endcomment %}`. A tag or a variable does not — put its
opening and closing markers back on one line.

Bad — all three render as visible text:
```html
{# This explains the block below
   and continues on a second line #}

{% if user.is_staff
      and user.is_active %}

{{ user.profile.display_name
   |default:user.email }}
```

Good:
```html
{% comment %}
This explains the block below
and continues on a second line.
{% endcomment %}
{# single-line note #}

{% if user.is_staff and user.is_active %}

{{ user.profile.display_name|default:user.email }}
```

Three surfaces are covered, because all three are compiled by
`django.template.Template`:

| Surface | Enforced by |
|---|---|
| `templates/**/*.html` | `content/tests/test_template_comment_lint.py` |
| `email_app/email_templates/*.md` | `content/tests/test_template_comment_lint.py` |
| Operator-authored `EmailTemplateOverride` rows | `studio/views/email_templates.py` rejects the save |

The lint is a `@tag("core")` hard gate with no baseline. It scans both file
trees as raw text and fails on any multi-line or unclosed `{# #}`, `{% %}` or
`{{ }}`, naming the token form so the failure says which construct to fix. It
replaces the reactive per-page `assertNotContains` guards on the opening
marker, which missed untested branches. Nothing else is scanned: `.py`,
`_docs/`, `specs/`, `.claude/` and `skills/` carry legitimate lone opening
markers in prose and string literals.

Operator copy in `EmailTemplateOverride` cannot be reached by a file lint, so
the Studio email-template editor applies the identical rule from
`content/utils/template_comments.py` — the single implementation both callers
import — and rejects the save with a line-numbered message instead of writing
the row.

Helpers that scan template source must mirror the same tokenizer. Do not add
`re.DOTALL` to a `{# #}` or opening-tag pattern: a permissive pattern masks a
region Django really renders, which makes the helper structurally blind to this
bug. See `_clean_snippet` in `studio/tests/test_studio_table_responsive.py`,
`_strip_non_control_regions` in `studio/tests/test_form_components.py`, and
`_mask_ignored_regions` in `studio/tests/test_studio_header_row_consistency.py`.


## Rule 22: Template changes pass the design-system guards

Any template change runs the design-system guards: `content.tests.test_design_layout_lint` and `content.tests.test_design_system_lint` (both selected by `make test-affected` for any `templates/**` change) and, when a member surface changed, `playwright_tests/test_design_layout_guard.py` (core). Reviewers also check new layouts against `_docs/design-system.md` rule by rule; a brief or orchestrator instruction never overrides the design system, and a conflicting brief is flagged, not followed.

---

## Testing operations runbook

Procedures for running, gating, and maintaining the suites live in
[`testing-operations.md`](testing-operations.md). Read the matching section
before you do any of these:

| Task | Section in `testing-operations.md` |
|---|---|
| Use the local database, seed data, or clean QA fixtures | Test databases, seed data, and local content |
| Reason about the 85% Deploy Dev coverage gate | Coverage gate (Deploy Dev sharded collection) |
| Touch a lexical AST ratchet or its manifest | Lexical AST occurrence ratchets |
| Change or debug `scripts/affected_tests.py` mapping | Affected-tests rule chain and maps |
| Add, move, or delete a Playwright test file | Exact Playwright owner baseline |
| Change CI shard timing, schedules, or the dev-environment suite | Playwright scheduling and telemetry |
| Run Playwright concurrently, in parallel, or with a custom port | Playwright concurrency and isolation |
| Run the opt-in live LLM-judge or live Slack tests | Live LLM-judge tests, Live Slack announcement test |

Code comments and marker help that cite this file by heading still resolve:
"Running Playwright in isolation / parallel across worktrees" and the live-test
markers in `pyproject.toml` are in `testing-operations.md`; "Marker taxonomy"
means "Special Playwright and platform markers" below.

---

## Affected-tests selection (`make test-affected`)

Per-issue local verification runs the tests the diff can plausibly break --
never the whole Django suite. `scripts/affected_tests.py` derives that scope
deterministically so neither the software engineer nor the tester has to guess
(or quietly escalate to `manage.py test`).

```bash
uv run python scripts/affected_tests.py            # print the plan, run nothing
uv run python scripts/affected_tests.py --json     # machine-readable plan
make test-affected                                 # run exactly the emitted commands
```

The helper is stdlib-only and never imports Django, so it runs in well under a
second. It diffs against `git merge-base origin/main HEAD` and unions in
unstaged, staged, and untracked files, because SWE work is still uncommitted
when the tester reviews it. Exit code 0 means a plan was produced (with
`--run`, the worst exit code of the executed commands); exit code 2 means git
failed.

### Why not just run everything locally

The full Django suite is ~17,200 tests. That figure is a rationale, so re-derive
it rather than copying it forward -- it was quoted as "~14,800" for months after
it stopped being true. Discovery only, no run, no database:

```bash
DJANGO_SETTINGS_MODULE=website.settings uv run python -c "
import django; django.setup()
from django.conf import settings
from django.test.utils import get_runner
print(get_runner(settings)(verbosity=0).build_suite([]).countTestCases())"
```

CI already runs that suite on every push to
main (`.github/workflows/deploy-dev.yml`) and blocks the deploy on failure, and
the full Playwright suite runs every 3 hours
(`.github/workflows/scheduled-playwright.yml`). Repeating either locally adds
no coverage; with several agents on one box it just produces contention. This
is also why the emitted Django command uses `--parallel 4` rather than a bare
`--parallel`: bare spawns one worker per core, which is what melted a 12-core
box running concurrent agents. CI uses the same bounded value.

The rule chain, repo-wide guards, guarded doc artifacts, curated hub-module
map, and one-hop reverse-import expansion are in
[`testing-operations.md`](testing-operations.md) ("Affected-tests rule chain
and maps"). The escalation table below stays here and is authoritative.

### Escalation table (authoritative)

This table is the source of truth for local Playwright escalation and
supersedes the prose list that used to live in `_docs/PROCESS.md`. It is
encoded as `ESCALATION_TRIGGERS` in `scripts/affected_tests.py` and pinned by
`tests/test_affected_tests.py`.

| Trigger | Reason |
|---|---|
| `playwright_tests/conftest.py` | shared fixtures |
| `tests/fixtures.py` | shared fixtures |
| `content/access.py`, `content/tier_config.py`, `accounts/gating.py`, `playwright_tests/test_access_control.py` | access-control matrix |
| `payments/tier_state.py`, `payments/stripe_links.py`, `payments/services/**`, `payments/views/**` (webhook handlers live under `payments/services/`) | payments wiring |
| `templates/includes/**`, `templates/_partials/**`, `templates/base.html`, `templates/studio/base.html`, `templates/studio/includes/**`, `templates/studio/_partials/**` | shared template fragments |
| `website/**` except `website/tests.py` and `website/tests/**`, `accounts/context_processors.py`, `accounts_ext/session_backend.py` (backs `SESSION_ENGINE`) | every-request/every-page surface; test-only website files retain broad Django coverage plus core Playwright |
| `tailwind.config.js` | content-purge config, can strip classes on any page |
| `integrations/middleware.py` | every request |
| `pyproject.toml`, `uv.lock` | soft trigger -- note only, does not force full local Playwright |

Note that `payments/tests/**` deliberately does not escalate: editing a
payments test is not the same as editing payments wiring.

The Studio-wide surfaces (`templates/studio/base.html` -- the shell every
Studio page extends -- plus `templates/studio/includes/**` and
`templates/studio/_partials/**`) are escalation triggers only and are
deliberately kept out of `SHARED_TEMPLATE_GLOBS`: rule 2 flags full Playwright
and lets the path fall through, so rule 10 keeps mapping them to the owning
`studio` app. Listing them in `SHARED_TEMPLATE_GLOBS` instead would re-route
the Django target to `content` (the shared-fragment branch runs before the
owning-app branch inside rule 10) and silently drop the studio tests.
Ordinary per-page Studio templates (`templates/studio/courses/**`,
`templates/studio/crm/**`, ...) keep the core-Playwright default -- their
blast radius is one page, not the shell.

### Working with the plan

- Read the plan before trusting it. `WARN unmapped:` means a path matched no
  rule; `NOTE broad-impact:` means the cap fired.
- Do not widen the scope by hand. If the plan misses tests that the change can
  break, that is a bug in the mapping: fix `scripts/affected_tests.py` and its
  self-test, or file an issue. Substituting a bigger run hides the bug.
- Keep `tests/test_affected_tests.py` in step with any map change. It is tagged
  `core`, so `make test-core` catches drift.

---

## Core test subset (`make test-core`)

The full Django suite is ~17,200 tests. For the inner loop (TDD, quick sanity
checks) we maintain a tagged subset that runs in well under a minute, and the
affected-tests plan above uses it as its fail-closed fallback.

```bash
make test-core          # tagged subset, ~30s, parallel
make test               # full suite -- CI-only by default
```

`make test-core` runs `python manage.py test --tag=core --parallel`. CI runs
the full suite on every push to main and blocks the deploy on failure, so
`make test` is not part of the local per-issue loop -- use `make test-affected`
(see "Affected-tests selection" above) and leave the exhaustive run to CI
unless Alexey explicitly asks for it locally.

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
make test-playwright-core    # ~900+ tests, runs 4-way parallel via pytest-xdist
make test-playwright         # full suite, runs on schedule (every 3h)
make test-playwright-manual-visual
```

The core subset has grown past 900 tests (~950 at the time of writing; run
`make test-playwright-core` and read the summary line for the current count).
It ran ~93 min serially before #1470; the Makefile targets now run it under
pytest-xdist. See "Parallel Playwright
with pytest-xdist (`PLAYWRIGHT_XDIST_WORKERS`)" in [`testing-operations.md`](testing-operations.md) for the measured runtime,
the chosen worker default, and the isolation guarantees.

`make test-playwright-core` runs `pytest -m core playwright_tests/ -n 4
--dist loadfile -v` by default (override the worker count with
`PLAYWRIGHT_XDIST_WORKERS`). The
Deploy Dev workflow applies the same core selection with its default marker
exclusions across four parallel `playwright-core` matrix jobs:

```bash
pytest -m "core and not manual_visual and not slow_platform and not visual_regression" <shard files> -v --durations=25
```

The workflow sorts every `playwright_tests/test_*.py` file and assigns files
round-robin by index. The four file sets are deterministic and disjoint, and a
new matching test file is selected automatically without a maintained list.
`Deploy to Dev` remains blocked until every core shard succeeds.

A failure blocks the deploy. The scheduled workflow runs the broader Playwright
suite every 3 hours, skipped if no commits have landed since the last successful
run. That scheduled default is sharded across separate GitHub Actions matrix
jobs with the measured assignment described in `testing-operations.md`
("Measured scheduled full-suite assignment") and uses:

```bash
pytest -m "not manual_visual and not slow_platform" <shard files> -v --durations=25
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
| `visual_regression` | Automated CSS class / Tailwind utility / spacing / color / layout-density assertions (`px-5`, `min-h-*`, `bg-card`, `flex-col`, `max-w-7xl`, etc.). Distinct from `manual_visual`: these run unattended, they just shouldn't gate push. | Excluded from `make test`, `make test-core`, `make test-playwright`, `make test-playwright-core`, Deploy Dev, and `ci.yml`. In `scheduled-playwright.yml` the two legs run in separate jobs: the Playwright `@pytest.mark.visual_regression` leg is picked up by the `playwright-full` job's default markers (`not manual_visual and not slow_platform`), and the Django `@tag('visual_regression')` leg runs in the dedicated `django-visual-regression` job (`uv run python manage.py test --tag=visual_regression --parallel`), which fails to its own `[CI] Scheduled Django visual_regression suite failing` issue lineage. Run both legs on demand with `make test-visual-regression`. |
| `local_only` | Tests that require the local Django runner — direct ORM seeding, session-cookie injection, `create_user`/`create_staff_user`/`auth_context`/`create_session_for_user`/`ensure_tiers`/`ensure_site_config_tiers` helpers, or pytest-django DB fixtures. They cannot run against a deployed environment. | Included in every run that hits a local base URL (default `PLAYWRIGHT_BASE_URL` unset, or set to `127.0.0.1`/`localhost`). Excluded automatically from the dev-environment scheduled suite (`scheduled-playwright-dev.yml`) — `playwright_tests/conftest.py` skips them when `PLAYWRIGHT_BASE_URL` is non-local. |
| `creates_data` | Tests that POST to write endpoints (`/api/projects/submit`, `/accounts/register/`, `/api/newsletter/subscribe`, etc.) without using an account from our local test fixtures. These would leave real rows behind on a shared dev DB. | Same gating as `local_only`: included locally, excluded from the dev-environment scheduled suite. |

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
- Stripe checkout/membership redirect smoke, including the billing toggle.
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

### Writing order-independent Playwright tests

`--dist loadfile` changes which module runs after which. A test that only
passed because of where it landed in the alphabet is now a coin flip, so two
long-standing hazards became visible and are fixed at the harness level:

- Process-wide `IntegrationSetting` cache. `integrations.config` memoizes every
  DB override in a module-level dict, and DB overrides beat `settings` and env
  inside `get_config()`. A `django_db(transaction=True)` teardown empties the
  table but nothing invalidated that cache, so a value one module seeded kept
  shadowing every later `settings.X = ...` override in the same process. An
  autouse `_reset_integration_config_cache` fixture in
  `playwright_tests/conftest.py` now resets it around every test, in memory
  only (no DB write, no cross-process stamp), via
  `integrations.config.reset_local_config_cache()`. Do not add another
  hand-rolled `clear_config_cache()` fixture to a new module — the conftest
  owns this now.
- Seeding must COMMIT to be visible. The in-process `runserver` thread reads
  through its own database connection, so anything written inside
  pytest-django's plain `django_db` atomic block is invisible to the browser.
  A test that seeds rows and then navigates to a page that reads them must use
  `@pytest.mark.django_db(transaction=True)`. A seeding fixture should request
  `transactional_db` explicitly so it is ordered after pytest-django's database
  setup.

If a new Playwright test passes alone and fails inside `make test-playwright`,
suspect one of those two before suspecting parallelism itself.
