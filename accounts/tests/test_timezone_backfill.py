"""Tests for the passive browser-timezone backfill flag (issue #961).

The base template runs a one-shot client-side backfill only when the
``needs_timezone_backfill`` context flag is True. That flag is produced by
``accounts.context_processors.timezone_backfill`` and is True only when the
user is authenticated AND ``preferred_timezone`` is empty.

These tests pin down the server-side contract that decides whether the
client script fires at all:

- Anonymous visitors never get the flag (so the script never runs).
- Authenticated users with an empty ``preferred_timezone`` get the flag.
- Authenticated users with a saved ``preferred_timezone`` do NOT get the
  flag (so passive detection never fires for them).

The JavaScript behaviour itself (calling ``Intl`` and POSTing) is covered
by the Playwright scenarios; here we only verify the gate the page renders.
"""

from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, TestCase, override_settings, tag

from accounts.context_processors import timezone_backfill

User = get_user_model()

FAST_PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


@tag("core")
@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS)
class TimezoneBackfillContextProcessorTest(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_anonymous_user_does_not_need_backfill(self):
        request = self.factory.get("/")
        request.user = AnonymousUser()
        self.assertFalse(
            timezone_backfill(request)["needs_timezone_backfill"]
        )

    def test_authenticated_empty_timezone_needs_backfill(self):
        user = User.objects.create_user(email="empty-tz@example.com")
        self.assertEqual(user.preferred_timezone, "")
        request = self.factory.get("/")
        request.user = user
        self.assertTrue(
            timezone_backfill(request)["needs_timezone_backfill"]
        )

    def test_authenticated_with_timezone_does_not_need_backfill(self):
        user = User.objects.create_user(email="set-tz@example.com")
        user.preferred_timezone = "Europe/Berlin"
        user.save(update_fields=["preferred_timezone"])
        request = self.factory.get("/")
        request.user = user
        self.assertFalse(
            timezone_backfill(request)["needs_timezone_backfill"]
        )

    def test_account_view_suppresses_passive_backfill(self):
        """The Account page owns its own detect-and-preview logic (#582/#596).

        The passive backfill must never fire there -- it would silently
        persist the browser zone on mere page view, breaking the
        informational-only contract. The flag is suppressed by view name.
        """
        user = User.objects.create_user(email="account-empty-tz@example.com")
        self.assertEqual(user.preferred_timezone, "")
        request = self.factory.get("/account/")
        request.user = user
        request.resolver_match = SimpleNamespace(view_name="account")
        self.assertFalse(
            timezone_backfill(request)["needs_timezone_backfill"]
        )

    def test_other_authenticated_view_keeps_passive_backfill(self):
        """Non-account authenticated pages must keep the passive backfill."""
        user = User.objects.create_user(email="home-empty-tz@example.com")
        request = self.factory.get("/")
        request.user = user
        request.resolver_match = SimpleNamespace(view_name="home")
        self.assertTrue(
            timezone_backfill(request)["needs_timezone_backfill"]
        )


@tag("core")
@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS)
class TimezoneBackfillRenderTest(TestCase):
    """The rendered page exposes the flag to the client script correctly."""

    def test_empty_tz_user_renders_flag_true_on_non_account_page(self):
        user = User.objects.create_user(
            email="render-empty@example.com", password="test1234"
        )
        self.client.force_login(user)
        # The home page is a regular authenticated, non-account page where
        # the passive backfill is allowed to fire.
        content = self.client.get("/").content.decode()
        # The base-template script derives the gate from this exact token.
        self.assertIn("needsTimezoneBackfill = true && true", content)

    def test_empty_tz_user_renders_flag_false_on_account_page(self):
        """The Account page suppresses the passive backfill (#582/#596)."""
        user = User.objects.create_user(
            email="render-empty-account@example.com", password="test1234"
        )
        self.client.force_login(user)
        content = self.client.get("/account/").content.decode()
        # Even though preferred_timezone is empty, the account view's own
        # detect-and-preview logic owns timezone here, so the passive
        # backfill gate must render false.
        self.assertIn("needsTimezoneBackfill = true && false", content)

    def test_set_tz_user_renders_flag_false(self):
        user = User.objects.create_user(
            email="render-set@example.com", password="test1234"
        )
        user.preferred_timezone = "America/New_York"
        user.save(update_fields=["preferred_timezone"])
        self.client.force_login(user)
        content = self.client.get("/account/").content.decode()
        self.assertIn("needsTimezoneBackfill = true && false", content)

    def test_anonymous_renders_flag_false(self):
        content = self.client.get("/").content.decode()
        self.assertIn("needsTimezoneBackfill = false && false", content)
