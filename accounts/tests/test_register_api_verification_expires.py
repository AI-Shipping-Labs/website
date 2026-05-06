"""Tests for the unverified-account lifecycle on the register/verify
endpoints (issue #452).

Covers:
- ``register_api`` populates ``verification_expires_at`` on creation,
  using ``UNVERIFIED_USER_TTL_DAYS`` (default 7).
- ``verify_email_api`` clears ``verification_expires_at`` on success.
- Social signups bypass the field entirely (auto-verified).
- The TTL setting from Studio is honored.
"""

import datetime
import json
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import User
from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting


class RegisterApiVerificationExpiresTest(TestCase):
    """``register_api`` sets ``verification_expires_at`` on creation."""

    url = "/api/register"

    def setUp(self):
        clear_config_cache()

    def _post(self, email="new@example.com", password="secure1234"):
        return self.client.post(
            self.url,
            data=json.dumps({"email": email, "password": password}),
            content_type="application/json",
        )

    @patch("accounts.views.auth._send_verification_email")
    @patch("accounts.views.auth._probe_slack_membership_on_signup")
    def test_register_api_sets_verification_expires_at(self, _probe, _send):
        """Default 7-day window is applied on email signup."""
        before = timezone.now()
        resp = self._post("expires7@example.com")
        after = timezone.now()
        self.assertEqual(resp.status_code, 201)

        user = User.objects.get(email="expires7@example.com")
        self.assertIsNotNone(user.verification_expires_at)
        # Roughly now + 7 days, allowing for the wallclock spread of the
        # test itself.
        lower = before + datetime.timedelta(days=7) - datetime.timedelta(seconds=5)
        upper = after + datetime.timedelta(days=7) + datetime.timedelta(seconds=5)
        self.assertGreaterEqual(user.verification_expires_at, lower)
        self.assertLessEqual(user.verification_expires_at, upper)
        # And the user is genuinely unverified — the field would be a
        # no-op otherwise.
        self.assertFalse(user.email_verified)

    @patch("accounts.views.auth._send_verification_email")
    @patch("accounts.views.auth._probe_slack_membership_on_signup")
    def test_verify_email_clears_verification_expires_at(self, _probe, _send):
        """A successful email verification cancels the auto-purge."""
        from accounts.views.auth import _generate_verification_token

        user = User.objects.create_user(
            email="tobeverified@example.com",
            password="secure1234",
            verification_expires_at=timezone.now() + datetime.timedelta(days=5),
        )
        self.assertFalse(user.email_verified)

        token = _generate_verification_token(user.pk)
        resp = self.client.get(f"/api/verify-email?token={token}")
        self.assertEqual(resp.status_code, 200)

        user.refresh_from_db()
        self.assertTrue(user.email_verified)
        self.assertIsNone(user.verification_expires_at)

    @patch("accounts.views.auth._send_verification_email")
    @patch("accounts.views.auth._probe_slack_membership_on_signup")
    @override_settings(UNVERIFIED_USER_TTL_DAYS="14")
    def test_register_api_respects_ttl_config(self, _probe, _send):
        """Operator-set TTL flows through to the User row."""
        clear_config_cache()
        before = timezone.now()
        resp = self._post("expires14@example.com")
        after = timezone.now()
        self.assertEqual(resp.status_code, 201)

        user = User.objects.get(email="expires14@example.com")
        lower = before + datetime.timedelta(days=14) - datetime.timedelta(seconds=5)
        upper = after + datetime.timedelta(days=14) + datetime.timedelta(seconds=5)
        self.assertGreaterEqual(user.verification_expires_at, lower)
        self.assertLessEqual(user.verification_expires_at, upper)

    @patch("accounts.views.auth._send_verification_email")
    @patch("accounts.views.auth._probe_slack_membership_on_signup")
    def test_register_api_respects_ttl_from_studio_setting(self, _probe, _send):
        """The IntegrationSetting (Studio-saved) overrides the default."""
        IntegrationSetting.objects.create(
            key="UNVERIFIED_USER_TTL_DAYS",
            value="3",
        )
        clear_config_cache()

        try:
            before = timezone.now()
            resp = self._post("expires3@example.com")
            after = timezone.now()
            self.assertEqual(resp.status_code, 201)

            user = User.objects.get(email="expires3@example.com")
            lower = before + datetime.timedelta(days=3) - datetime.timedelta(seconds=5)
            upper = after + datetime.timedelta(days=3) + datetime.timedelta(seconds=5)
            self.assertGreaterEqual(user.verification_expires_at, lower)
            self.assertLessEqual(user.verification_expires_at, upper)
        finally:
            clear_config_cache()


class SocialSignupSkipsVerificationExpiresTest(TestCase):
    """OAuth signups never populate ``verification_expires_at``.

    Social providers are trusted to verify the email, so the
    ``social_account_added`` signal handler flips ``email_verified=True``
    immediately and the account is exempt from the purge. Verifies the
    handler does not introduce ``verification_expires_at`` as a side
    effect either.
    """

    def test_social_signup_skips_verification_expires(self):
        from accounts.signals import mark_email_verified_on_social_signup

        user = User.objects.create_user(
            email="social@example.com",
            password=None,
        )
        # Pre-state: a real social signup hits the handler with
        # email_verified=False and we expect the signal to flip it.
        self.assertFalse(user.email_verified)
        self.assertIsNone(user.verification_expires_at)

        sociallogin = MagicMock()
        sociallogin.user = user
        mark_email_verified_on_social_signup(
            sender=None,
            request=None,
            sociallogin=sociallogin,
        )

        user.refresh_from_db()
        self.assertTrue(user.email_verified)
        self.assertIsNone(user.verification_expires_at)
