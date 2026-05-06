"""Tests for ``accounts.tasks.purge_unverified_users`` (issue #452).

The purge job hard-deletes expired unverified email-signup accounts
that have done nothing else. Each test pins a single safety gate so a
regression in any of them surfaces a clear failure.
"""

import datetime

from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from accounts.tasks import purge_unverified_users
from email_app.models import EmailLog


def _make_unverified(email, *, expires_offset_hours, **extra):
    """Fixture: an unverified user with a verification window relative to now."""
    return User.objects.create_user(
        email=email,
        password="secure1234",
        email_verified=False,
        verification_expires_at=timezone.now()
        + datetime.timedelta(hours=expires_offset_hours),
        **extra,
    )


class PurgeUnverifiedUsersTest(TestCase):
    """Hard-delete only when the user is genuinely abandoned."""

    def test_purge_deletes_expired_unverified_user(self):
        user = _make_unverified("expired@example.com", expires_offset_hours=-24)
        result = purge_unverified_users()

        self.assertEqual(result["deleted"], 1)
        self.assertFalse(User.objects.filter(pk=user.pk).exists())

    def test_purge_keeps_unexpired_unverified_user(self):
        user = _make_unverified("future@example.com", expires_offset_hours=24)
        result = purge_unverified_users()

        self.assertEqual(result["deleted"], 0)
        self.assertTrue(User.objects.filter(pk=user.pk).exists())

    def test_purge_keeps_verified_user(self):
        user = User.objects.create_user(
            email="verified@example.com",
            password="secure1234",
            email_verified=True,
            verification_expires_at=timezone.now() - datetime.timedelta(days=1),
        )
        result = purge_unverified_users()

        self.assertEqual(result["deleted"], 0)
        self.assertTrue(User.objects.filter(pk=user.pk).exists())

    def test_purge_keeps_user_with_last_login(self):
        user = _make_unverified(
            "loggedin@example.com",
            expires_offset_hours=-24,
            last_login=timezone.now() - datetime.timedelta(days=30),
        )
        with self.assertLogs(
            "accounts.tasks.purge_unverified_users",
            level="WARNING",
        ) as logs:
            result = purge_unverified_users()

        self.assertEqual(result["deleted"], 0)
        self.assertTrue(User.objects.filter(pk=user.pk).exists())
        self.assertTrue(
            any("blocked by last_login" in msg for msg in logs.output),
            f"expected blocked-by-last_login warning, got {logs.output}",
        )

    def test_purge_keeps_user_with_stripe_customer_id(self):
        user = _make_unverified(
            "stripey@example.com",
            expires_offset_hours=-1,
            stripe_customer_id="cus_X",
        )
        with self.assertLogs(
            "accounts.tasks.purge_unverified_users",
            level="WARNING",
        ) as logs:
            result = purge_unverified_users()

        self.assertEqual(result["deleted"], 0)
        self.assertTrue(User.objects.filter(pk=user.pk).exists())
        self.assertTrue(
            any("blocked by stripe_customer_id" in msg for msg in logs.output),
            f"expected blocked-by-stripe warning, got {logs.output}",
        )

    def test_purge_keeps_user_with_subscription_id(self):
        user = _make_unverified(
            "subbed@example.com",
            expires_offset_hours=-1,
            subscription_id="sub_X",
        )
        with self.assertLogs(
            "accounts.tasks.purge_unverified_users",
            level="WARNING",
        ):
            result = purge_unverified_users()

        self.assertEqual(result["deleted"], 0)
        self.assertTrue(User.objects.filter(pk=user.pk).exists())

    def test_purge_keeps_user_with_email_log(self):
        user = _make_unverified("hadmail@example.com", expires_offset_hours=-2)
        EmailLog.objects.create(
            user=user,
            email_type="welcome",
            ses_message_id="abc",
        )

        with self.assertLogs(
            "accounts.tasks.purge_unverified_users",
            level="WARNING",
        ) as logs:
            result = purge_unverified_users()

        self.assertEqual(result["deleted"], 0)
        self.assertTrue(User.objects.filter(pk=user.pk).exists())
        # Should name the blocking reverse relation explicitly so an
        # operator can grep the log and find which downstream rows
        # exist.
        self.assertTrue(
            any("email_logs" in msg for msg in logs.output),
            f"expected blocker name in warning, got {logs.output}",
        )

    def test_purge_safe_for_users_with_null_verification_expires_at(self):
        """Legacy rows (pre-#452 migration) are exempt from purge."""
        user = User.objects.create_user(
            email="legacy@example.com",
            password="secure1234",
            email_verified=False,
            verification_expires_at=None,
        )
        result = purge_unverified_users()

        self.assertEqual(result["deleted"], 0)
        self.assertTrue(User.objects.filter(pk=user.pk).exists())

    def test_purge_processes_multiple_candidates_in_one_pass(self):
        """Purge does not stop at the first candidate."""
        a = _make_unverified("a@example.com", expires_offset_hours=-48)
        b = _make_unverified("b@example.com", expires_offset_hours=-12)
        keep = _make_unverified("keep@example.com", expires_offset_hours=12)

        result = purge_unverified_users()

        self.assertEqual(result["deleted"], 2)
        self.assertFalse(User.objects.filter(pk=a.pk).exists())
        self.assertFalse(User.objects.filter(pk=b.pk).exists())
        self.assertTrue(User.objects.filter(pk=keep.pk).exists())
