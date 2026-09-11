"""Tests for ``accounts.tasks.remind_unverified_users`` (issue #452).

The reminder job is one-shot per user, gated on the same activity
checks as the purge so we never nudge users with a real account or
unsubscribe override.

A1.2: sends go through the package mail app; the ``EmailLog`` audit row
appears once the pending delivery is drained, and the verify URL is
minted in the delivery worker from the durable row.
"""

import datetime
from unittest.mock import patch

from community_base.mail.models import EmailDelivery
from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from accounts.tasks import remind_unverified_users
from accounts.tasks.remind_unverified_users import (
    SIGNUP_REMINDER_TEMPLATE_NAME,
    SUBSCRIBE_REMINDER_TEMPLATE_NAME,
)
from email_app.models import EmailLog
from email_app.testing import StubSESClient, deliver_pending_mail

# Issue #767: the reminder template is now picked per user based on the
# slug of the most recent verification EmailLog. The default for tests
# that don't pre-seed a verification log is the signup reminder.
REMINDER_TEMPLATE_NAME = SIGNUP_REMINDER_TEMPLATE_NAME


class RemindUnverifiedUsersTest(TestCase):
    def _make_user(self, email, *, expires_offset_hours, **extra):
        return User.objects.create_user(
            email=email,
            password="secure1234",
            email_verified=False,
            verification_expires_at=timezone.now()
            + datetime.timedelta(hours=expires_offset_hours),
            **extra,
        )

    def test_reminder_sent_when_expiry_within_24h(self):
        user = self._make_user("soon@example.com", expires_offset_hours=12)
        stub = StubSESClient()
        with patch(
            "community_base.mail.backends.ses_local.configured_client",
            return_value=stub,
        ):
            result = remind_unverified_users()
            deliver_pending_mail()
        self.assertEqual(result["sent"], 1)
        log = EmailLog.objects.get(
            user=user, email_type=REMINDER_TEMPLATE_NAME,
        )
        self.assertEqual(log.user, user)

        # The signed verify URL is minted in the worker from the durable
        # delivery, never stored with the send.
        delivery = EmailDelivery.objects.get(purpose=REMINDER_TEMPLATE_NAME)
        self.assertNotIn("verify_url", delivery.context_data)
        self.assertEqual(len(stub.calls), 1)
        body_html = stub.calls[0]["Content"]["Simple"]["Body"]["Html"]["Data"]
        self.assertIn("/api/verify-email?token=", body_html)

        user.refresh_from_db()
        self.assertIsNotNone(user.verification_reminder_sent_at)

    def test_reminder_not_sent_outside_24h_window(self):
        """Users expiring later than 24h get no nudge yet."""
        user = self._make_user("later@example.com", expires_offset_hours=72)

        result = remind_unverified_users()
        self.assertEqual(result["sent"], 0)

        self.assertEqual(EmailDelivery.objects.count(), 0)
        self.assertFalse(
            EmailLog.objects.filter(
                user=user, email_type=REMINDER_TEMPLATE_NAME,
            ).exists()
        )

    def test_reminder_skips_already_expired(self):
        """A user past expiry is the purge job's problem, not ours."""
        user = self._make_user("past@example.com", expires_offset_hours=-1)

        result = remind_unverified_users()
        self.assertEqual(result["sent"], 0)

        self.assertFalse(
            EmailLog.objects.filter(
                user=user, email_type=REMINDER_TEMPLATE_NAME,
            ).exists()
        )

    def test_reminder_not_sent_twice(self):
        user = self._make_user("once@example.com", expires_offset_hours=12)
        user.verification_reminder_sent_at = (
            timezone.now() - datetime.timedelta(hours=2)
        )
        user.save(update_fields=["verification_reminder_sent_at"])

        result = remind_unverified_users()
        self.assertEqual(result["sent"], 0)
        self.assertEqual(
            EmailLog.objects.filter(
                user=user, email_type=REMINDER_TEMPLATE_NAME,
            ).count(),
            0,
        )

    def test_reminder_skips_unsubscribed_users(self):
        user = self._make_user(
            "unsub@example.com",
            expires_offset_hours=12,
            unsubscribed=True,
        )

        result = remind_unverified_users()
        self.assertEqual(result["sent"], 0)
        self.assertEqual(EmailDelivery.objects.count(), 0)
        user.refresh_from_db()
        # Unsubscribed users must not have the timestamp marked, so we
        # can resume reminders if they ever resubscribe before expiry.
        self.assertIsNone(user.verification_reminder_sent_at)

    def test_reminder_skips_users_who_logged_in(self):
        user = self._make_user(
            "session@example.com",
            expires_offset_hours=12,
            last_login=timezone.now() - datetime.timedelta(days=1),
        )

        result = remind_unverified_users()
        self.assertEqual(result["sent"], 0)
        self.assertFalse(
            EmailLog.objects.filter(
                user=user, email_type=REMINDER_TEMPLATE_NAME,
            ).exists()
        )

    def test_reminder_skips_already_verified_user(self):
        """email_verified=True must not receive a reminder even if the field is set."""
        user = self._make_user("verified@example.com", expires_offset_hours=12)
        user.email_verified = True
        user.save(update_fields=["email_verified"])

        result = remind_unverified_users()
        self.assertEqual(result["sent"], 0)
        self.assertFalse(
            EmailLog.objects.filter(
                user=user, email_type=REMINDER_TEMPLATE_NAME,
            ).exists()
        )

    def test_suppressed_delivery_leaves_timestamp_unmarked(self):
        """A preference-resolver refusal maps to the old declined-send path."""
        user = self._make_user("suppressed@example.com", expires_offset_hours=12)
        declined = EmailDelivery(
            purpose=REMINDER_TEMPLATE_NAME,
            state=EmailDelivery.State.SUPPRESSED,
        )
        with patch(
            "email_app.package_mail.send_package_mail", return_value=declined,
        ):
            result = remind_unverified_users()

        self.assertEqual(result["sent"], 0)
        self.assertEqual(result["skipped"], 1)
        user.refresh_from_db()
        self.assertIsNone(user.verification_reminder_sent_at)


class RemindUnverifiedUsersPerFlowTemplateTest(TestCase):
    """Issue #767: the reminder template is chosen per user based on
    the slug of the most recent verification EmailLog.
    """

    def _make_user(self, email, *, expires_offset_hours=12, **extra):
        return User.objects.create_user(
            email=email,
            password="secure1234",
            email_verified=False,
            verification_expires_at=timezone.now()
            + datetime.timedelta(hours=expires_offset_hours),
            **extra,
        )

    def _sweep_and_drain(self):
        result = remind_unverified_users()
        deliver_pending_mail()
        return result

    def test_signup_flow_user_gets_signup_reminder(self):
        user = self._make_user("signup-path@example.com")
        EmailLog.objects.create(
            user=user,
            email_type="email_verification_signup",
            ses_message_id="ses-signup-orig",
        )

        result = self._sweep_and_drain()
        self.assertEqual(result["sent"], 1)

        # The reminder slug must be the signup-flow one, not subscribe.
        self.assertTrue(
            EmailLog.objects.filter(
                user=user, email_type=SIGNUP_REMINDER_TEMPLATE_NAME,
            ).exists()
        )
        self.assertFalse(
            EmailLog.objects.filter(
                user=user, email_type=SUBSCRIBE_REMINDER_TEMPLATE_NAME,
            ).exists()
        )

    def test_subscribe_flow_user_gets_subscribe_reminder(self):
        user = self._make_user("subscribe-path@example.com")
        EmailLog.objects.create(
            user=user,
            email_type="email_verification_subscribe",
            ses_message_id="ses-sub-orig",
        )

        result = self._sweep_and_drain()

        self.assertEqual(result["sent"], 1)
        self.assertTrue(
            EmailLog.objects.filter(
                user=user, email_type=SUBSCRIBE_REMINDER_TEMPLATE_NAME,
            ).exists()
        )
        self.assertFalse(
            EmailLog.objects.filter(
                user=user, email_type=SIGNUP_REMINDER_TEMPLATE_NAME,
            ).exists()
        )

    def test_user_without_prior_verification_log_defaults_to_signup_reminder(self):
        """Safe default for legacy users whose original send predates the split."""
        user = self._make_user("no-prior-log@example.com")

        result = self._sweep_and_drain()

        self.assertEqual(result["sent"], 1)
        self.assertTrue(
            EmailLog.objects.filter(
                user=user, email_type=SIGNUP_REMINDER_TEMPLATE_NAME,
            ).exists()
        )

    def test_most_recent_log_wins_when_user_has_both(self):
        """If a user has both flow logs, the latest one decides the reminder."""
        user = self._make_user("both-flows@example.com")
        # Older signup log.
        older = EmailLog.objects.create(
            user=user,
            email_type="email_verification_signup",
            ses_message_id="ses-signup-old",
        )
        older.sent_at = timezone.now() - datetime.timedelta(hours=6)
        older.save(update_fields=["sent_at"])
        # Newer subscribe log.
        newer = EmailLog.objects.create(
            user=user,
            email_type="email_verification_subscribe",
            ses_message_id="ses-sub-new",
        )
        newer.sent_at = timezone.now() - datetime.timedelta(hours=1)
        newer.save(update_fields=["sent_at"])

        result = self._sweep_and_drain()

        self.assertEqual(result["sent"], 1)
        # Latest log was subscribe -> subscribe reminder.
        self.assertTrue(
            EmailLog.objects.filter(
                user=user, email_type=SUBSCRIBE_REMINDER_TEMPLATE_NAME,
            ).exists()
        )
