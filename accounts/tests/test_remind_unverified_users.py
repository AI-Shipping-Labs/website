"""Tests for ``accounts.tasks.remind_unverified_users`` (issue #452).

The reminder job is one-shot per user, gated on the same activity
checks as the purge so we never nudge users with a real account or
unsubscribe override.
"""

import datetime
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from accounts.tasks import remind_unverified_users
from accounts.tasks.remind_unverified_users import REMINDER_TEMPLATE_NAME
from email_app.models import EmailLog


def _fake_send(self, user, template_name, context=None):
    """Stand-in for ``EmailService.send`` that records an EmailLog row.

    Mirrors the production behavior closely enough for the reminder
    tests: returns ``None`` for unsubscribed users so the caller leaves
    ``verification_reminder_sent_at`` untouched, otherwise creates a
    log row and returns it. ``self`` is the bound EmailService and is
    intentionally unused.
    """
    if getattr(user, "unsubscribed", False):
        return None
    return EmailLog.objects.create(
        user=user,
        email_type=template_name,
        ses_message_id="ses-test",
    )


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

    @patch("email_app.services.email_service.EmailService.send", new=_fake_send)
    def test_reminder_sent_when_expiry_within_24h(self):
        user = self._make_user("soon@example.com", expires_offset_hours=12)

        result = remind_unverified_users()
        self.assertEqual(result["sent"], 1)

        log = EmailLog.objects.get(
            user=user, email_type=REMINDER_TEMPLATE_NAME,
        )
        self.assertEqual(log.user, user)

        user.refresh_from_db()
        self.assertIsNotNone(user.verification_reminder_sent_at)

    @patch("email_app.services.email_service.EmailService.send", new=_fake_send)
    def test_reminder_not_sent_outside_24h_window(self):
        """Users expiring later than 24h get no nudge yet."""
        user = self._make_user("later@example.com", expires_offset_hours=72)

        result = remind_unverified_users()
        self.assertEqual(result["sent"], 0)

        self.assertFalse(
            EmailLog.objects.filter(
                user=user, email_type=REMINDER_TEMPLATE_NAME,
            ).exists()
        )

    @patch("email_app.services.email_service.EmailService.send", new=_fake_send)
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

    @patch("email_app.services.email_service.EmailService.send", new=_fake_send)
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

    @patch("email_app.services.email_service.EmailService.send", new=_fake_send)
    def test_reminder_skips_unsubscribed_users(self):
        user = self._make_user(
            "unsub@example.com",
            expires_offset_hours=12,
            unsubscribed=True,
        )

        result = remind_unverified_users()
        self.assertEqual(result["sent"], 0)
        self.assertFalse(
            EmailLog.objects.filter(
                user=user, email_type=REMINDER_TEMPLATE_NAME,
            ).exists()
        )
        user.refresh_from_db()
        # Unsubscribed users must not have the timestamp marked, so we
        # can resume reminders if they ever resubscribe before expiry.
        self.assertIsNone(user.verification_reminder_sent_at)

    @patch("email_app.services.email_service.EmailService.send", new=_fake_send)
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

    @patch("email_app.services.email_service.EmailService.send", new=_fake_send)
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
