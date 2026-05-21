"""Tests for ``accounts.tasks.purge_unverified_users`` (issue #452).

The purge job hard-deletes expired unverified email-signup accounts
that have done nothing else. Each test pins a single safety gate so a
regression in any of them surfaces a clear failure.
"""

import datetime

from django.test import TestCase, override_settings
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


def _make_eager_candidate(email, *, bounce_age_hours, **extra):
    """Fixture: unverified user with a permanent bounce at the given age.

    Mirrors the production state set by ``_mark_permanent_bounce`` so
    each eager-bucket test exercises one safety gate without leaking
    state across tests.
    """
    return User.objects.create_user(
        email=email,
        password="secure1234",
        email_verified=False,
        bounce_state=User.BounceState.PERMANENT,
        bounce_recorded_at=timezone.now() - datetime.timedelta(
            hours=bounce_age_hours,
        ),
        last_bounce_diagnostic="550 5.1.1 No such mailbox",
        **extra,
    )


class EagerBounceBucketTest(TestCase):
    """Pass B: unverified users with a permanent bounce older than 24h."""

    def test_eager_purge_deletes_old_permanent_bounce(self):
        """The ledger row blocks the standard bucket but not the eager one."""
        user = _make_eager_candidate(
            "dead@example.com",
            bounce_age_hours=48,
        )
        # The verification email IS the row that bounced -- it's why
        # the standard purge would skip the user. The eager bucket
        # ignores it.
        EmailLog.objects.create(
            user=user,
            email_type="email_verification",
            ses_message_id="ses-eager-1",
        )

        result = purge_unverified_users()

        self.assertEqual(result["deleted_eager"], 1)
        self.assertEqual(result["deleted_standard"], 0)
        self.assertEqual(result["deleted"], 1)
        self.assertFalse(User.objects.filter(pk=user.pk).exists())

    def test_eager_purge_skips_fresh_permanent_bounce(self):
        """Inside the 24h grace window the user stays put."""
        user = _make_eager_candidate(
            "wait@example.com",
            bounce_age_hours=1,
        )

        result = purge_unverified_users()

        self.assertEqual(result["deleted_eager"], 0)
        self.assertEqual(result["deleted"], 0)
        self.assertTrue(User.objects.filter(pk=user.pk).exists())

    def test_eager_purge_skips_verified_user(self):
        """Eager bucket only touches unverified rows."""
        user = User.objects.create_user(
            email="verified-bounced@example.com",
            password="secure1234",
            email_verified=True,
            bounce_state=User.BounceState.PERMANENT,
            bounce_recorded_at=timezone.now() - datetime.timedelta(days=30),
        )

        result = purge_unverified_users()

        self.assertEqual(result["deleted_eager"], 0)
        self.assertTrue(User.objects.filter(pk=user.pk).exists())

    def test_eager_purge_skips_user_with_stripe_customer_id(self):
        """Payments still block even when the email is dead."""
        user = _make_eager_candidate(
            "paid-dead@example.com",
            bounce_age_hours=48,
            stripe_customer_id="cus_X",
        )

        result = purge_unverified_users()

        self.assertEqual(result["deleted_eager"], 0)
        self.assertEqual(result["skipped_eager"], 1)
        self.assertTrue(User.objects.filter(pk=user.pk).exists())

    def test_eager_purge_skips_soft_bounce(self):
        """Soft state never triggers the eager bucket."""
        user = User.objects.create_user(
            email="soft@example.com",
            password="secure1234",
            email_verified=False,
            bounce_state=User.BounceState.SOFT,
            bounce_recorded_at=timezone.now() - datetime.timedelta(days=30),
        )

        result = purge_unverified_users()

        self.assertEqual(result["deleted_eager"], 0)
        self.assertEqual(result["deleted"], 0)
        self.assertTrue(User.objects.filter(pk=user.pk).exists())

    def test_purge_return_dict_has_all_six_counters(self):
        """Both buckets contribute to a shape that monitors can consume."""
        # One eager-bucket candidate, one standard-bucket candidate.
        eager_user = _make_eager_candidate(
            "eager-row@example.com", bounce_age_hours=48,
        )
        EmailLog.objects.create(
            user=eager_user,
            email_type="email_verification",
            ses_message_id="ses-eager-2",
        )
        standard_user = _make_unverified(
            "standard-row@example.com", expires_offset_hours=-48,
        )

        result = purge_unverified_users()

        expected_keys = {
            "deleted",
            "deleted_standard",
            "deleted_eager",
            "skipped",
            "skipped_standard",
            "skipped_eager",
        }
        self.assertEqual(set(result.keys()), expected_keys)

        self.assertEqual(result["deleted_standard"], 1)
        self.assertEqual(result["deleted_eager"], 1)
        # Legacy totals stay backwards-compatible.
        self.assertEqual(
            result["deleted"],
            result["deleted_standard"] + result["deleted_eager"],
        )
        self.assertEqual(
            result["skipped"],
            result["skipped_standard"] + result["skipped_eager"],
        )
        self.assertFalse(User.objects.filter(pk=eager_user.pk).exists())
        self.assertFalse(User.objects.filter(pk=standard_user.pk).exists())

    def test_eager_purge_emits_audit_log_with_email_and_recorded_at(self):
        """Each eager-bucket delete logs at INFO with audit fields."""
        user = _make_eager_candidate(
            "audit@example.com",
            bounce_age_hours=48,
        )
        recorded_iso = user.bounce_recorded_at.isoformat()

        with self.assertLogs(
            "accounts.tasks.purge_unverified_users",
            level="INFO",
        ) as logs:
            purge_unverified_users()

        # Find the per-row eager-purge audit line (not the summary).
        eager_lines = [m for m in logs.output if "Eager-purged" in m]
        self.assertTrue(eager_lines, f"no eager-purge audit line in {logs.output}")
        line = eager_lines[0]
        self.assertIn("audit@example.com", line)
        self.assertIn(recorded_iso, line)
        self.assertIn("550 5.1.1 No such mailbox", line)

    @override_settings(BOUNCE_PURGE_DELAY_HOURS=1)
    def test_eager_purge_honors_settings_override(self):
        """A 90-minute-old bounce is purged when the override drops to 1h."""
        user = _make_eager_candidate(
            "tweaked@example.com",
            bounce_age_hours=1.5,
        )

        result = purge_unverified_users()

        self.assertEqual(result["deleted_eager"], 1)
        self.assertFalse(User.objects.filter(pk=user.pk).exists())
