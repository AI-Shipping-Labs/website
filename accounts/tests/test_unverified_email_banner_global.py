"""Tests for the globalized unverified-email banner (issue #698).

The banner used to live only on ``/account/``. After #698 it is included
from ``templates/includes/header.html`` so every page that uses the global
header renders it for authenticated, unverified users. The Django-side
contract this file pins down:

- Anonymous visitors at ``/`` see no banner.
- Verified logged-in users at ``/`` see no banner AND the
  ``EmailLog`` query is not executed.
- Unverified logged-in users see the banner on ``/`` and on ``/account/``,
  rendered exactly once.
- The Resend form contains a hidden ``next`` input set to
  ``request.get_full_path()`` so Resend round-trips back to where the
  user was.
- POST to ``account_resend_verification`` with a safe ``next`` redirects
  there; a hostile ``next`` falls back to the ``account`` URL name.
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.db import connection
from django.test import TestCase, override_settings, tag
from django.test.utils import CaptureQueriesContext

from accounts.context_processors import unverified_email_banner
from email_app.models import EmailLog

User = get_user_model()

FAST_PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


@tag("core")
@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS)
class UnverifiedEmailBannerGlobalTest(TestCase):
    """Banner rendering across pages, not just /account/."""

    def setUp(self):
        cache.clear()

    def test_unverified_user_sees_banner_on_home(self):
        user = User.objects.create_user(
            email="unverified-home@example.com", password="test1234"
        )
        self.assertFalse(user.email_verified)
        self.client.force_login(user)
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode()
        # Banner ID is the canonical hook used by Playwright tests.
        self.assertIn('id="email-verification-banner"', content)
        self.assertIn("Verify your email", content)
        banner_start = content.index('id="email-verification-banner"')
        banner_tag_start = content.rfind("<", 0, banner_start)
        banner_tag_end = content.find(">", banner_start)
        banner_tag = content[banner_tag_start:banner_tag_end + 1]
        self.assertNotIn("mb-8", banner_tag)
        # Exactly one banner on the page.
        self.assertEqual(content.count('id="email-verification-banner"'), 1)

    def test_unverified_user_resend_form_has_next_to_current_path(self):
        """Resend form on a non-account page round-trips back to that page."""
        user = User.objects.create_user(
            email="unverified-next@example.com", password="test1234"
        )
        self.client.force_login(user)
        resp = self.client.get("/")
        content = resp.content.decode()
        # The hidden next input should carry the current path so Resend
        # redirects back here instead of always sending the user to /account/.
        self.assertIn(
            '<input type="hidden" name="next" value="/">',
            content,
        )

    def test_verified_user_no_banner_on_home(self):
        user = User.objects.create_user(
            email="verified-home@example.com", password="test1234"
        )
        user.email_verified = True
        user.save(update_fields=["email_verified"])
        self.client.force_login(user)
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode()
        self.assertNotIn('id="email-verification-banner"', content)

    def test_anonymous_visitor_no_banner_on_home(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode()
        self.assertNotIn('id="email-verification-banner"', content)

    def test_unverified_user_sees_banner_on_account(self):
        """Regression: /account/ still shows the banner, and only once.

        The inline copy in ``account.html`` was removed; the global include
        in ``header.html`` is now the single source of truth.
        """
        user = User.objects.create_user(
            email="unverified-account@example.com", password="test1234"
        )
        self.client.force_login(user)
        resp = self.client.get("/account/")
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode()
        self.assertEqual(content.count('id="email-verification-banner"'), 1)


@tag("core")
@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS)
class UnverifiedEmailBannerContextProcessorTest(TestCase):
    """Direct unit tests on the context processor's contract."""

    def test_returns_empty_for_anonymous(self):
        request = type("R", (), {"user": AnonymousUser()})()
        self.assertEqual(unverified_email_banner(request), {})

    def test_returns_empty_for_verified_user(self):
        user = User.objects.create_user(
            email="cp-verified@example.com", password="test1234"
        )
        user.email_verified = True
        user.save(update_fields=["email_verified"])
        request = type("R", (), {"user": user})()
        self.assertEqual(unverified_email_banner(request), {})

    def test_returns_latest_email_for_unverified_user(self):
        user = User.objects.create_user(
            email="cp-unverified@example.com", password="test1234"
        )
        log = EmailLog.objects.create(
            user=user,
            email_type="email_verification_signup",
        )
        request = type("R", (), {"user": user})()
        result = unverified_email_banner(request)
        self.assertIn("latest_verification_email", result)
        self.assertEqual(result["latest_verification_email"], log)

    def test_returns_none_when_no_email_log(self):
        user = User.objects.create_user(
            email="cp-no-log@example.com", password="test1234"
        )
        request = type("R", (), {"user": user})()
        result = unverified_email_banner(request)
        # Key is present but value is None when the user has no prior
        # verification email logged.
        self.assertEqual(result, {"latest_verification_email": None})

    def test_aggregates_across_both_verify_slugs(self):
        """Issue #767: the banner query unions both per-flow verify slugs
        so the timestamp surfaces regardless of which flow sent the latest
        verification email.
        """
        import datetime

        from django.utils import timezone

        user = User.objects.create_user(
            email="cp-both-flows@example.com", password="test1234"
        )
        # Older subscribe-flow send.
        older = EmailLog.objects.create(
            user=user,
            email_type="email_verification_subscribe",
        )
        older.sent_at = timezone.now() - datetime.timedelta(hours=6)
        older.save(update_fields=["sent_at"])
        # Newer signup-flow send (e.g. the user re-signed up).
        newer = EmailLog.objects.create(
            user=user,
            email_type="email_verification_signup",
        )
        newer.sent_at = timezone.now() - datetime.timedelta(minutes=5)
        newer.save(update_fields=["sent_at"])

        request = type("R", (), {"user": user})()
        result = unverified_email_banner(request)
        # The most recent log wins regardless of slug — banner does not
        # need to know which flow the user is in.
        self.assertEqual(result["latest_verification_email"], newer)

    def test_subscribe_flow_log_alone_surfaces_in_banner(self):
        """Issue #767: a user whose only verification log is the
        subscribe-flow slug still triggers the banner timestamp.
        """
        user = User.objects.create_user(
            email="cp-subscribe-only@example.com", password="test1234"
        )
        log = EmailLog.objects.create(
            user=user,
            email_type="email_verification_subscribe",
        )
        request = type("R", (), {"user": user})()
        result = unverified_email_banner(request)
        self.assertEqual(result["latest_verification_email"], log)

    def test_no_db_hit_for_verified_user(self):
        """Verified users must not pay the EmailLog query cost on every page."""
        user = User.objects.create_user(
            email="cp-noquery@example.com", password="test1234"
        )
        user.email_verified = True
        user.save(update_fields=["email_verified"])
        self.client.force_login(user)
        # Warm caches that might do unrelated lookups, then capture the
        # real page load. The point: no query touches the EmailLog table.
        self.client.get("/")
        with CaptureQueriesContext(connection) as captured:
            self.client.get("/")
        executed = " ".join(q["sql"] for q in captured.captured_queries)
        self.assertNotIn("email_app_emaillog", executed.lower())


@tag("core")
@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS)
class ResendVerificationNextRedirectTest(TestCase):
    """Confirms POST to resend honors the ``next`` field set by the partial."""

    def setUp(self):
        cache.clear()

    def test_resend_safe_next_redirects_back_to_that_page(self):
        user = User.objects.create_user(
            email="resend-safe@example.com", password="test1234"
        )
        self.client.force_login(user)
        # Stub the actual email send so the test doesn't talk to SES.
        with patch(
            "accounts.views.auth._send_verification_email"
        ) as send_mock:
            send_mock.return_value = EmailLog.objects.create(
                user=user,
                email_type="email_verification_signup",
            )
            resp = self.client.post(
                "/account/api/resend-verification",
                data={"next": "/courses/"},
            )
        # Safe in-site next is preserved.
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, "/courses/")

    def test_resend_hostile_next_falls_back_to_account(self):
        user = User.objects.create_user(
            email="resend-evil@example.com", password="test1234"
        )
        self.client.force_login(user)
        with patch(
            "accounts.views.auth._send_verification_email"
        ) as send_mock:
            send_mock.return_value = EmailLog.objects.create(
                user=user,
                email_type="email_verification_signup",
            )
            resp = self.client.post(
                "/account/api/resend-verification",
                data={"next": "//evil.com/steal"},
            )
        # Hostile next is dropped; redirect goes to the named ``account`` URL.
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, "/account/")
