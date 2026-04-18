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

Use `freezegun` or `time_machine` instead of `timezone.now() + timedelta(...)`.
Tests that compare against wall-clock time are race-condition prone on slow CI.

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
