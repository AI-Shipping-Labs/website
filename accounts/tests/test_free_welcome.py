"""Contract tests for ``accounts.services.free_welcome`` (A1.2 slice 2).

The welcome must stay at-most-once per member now that the ``EmailLog``
audit row is written asynchronously by the delivery worker: the durable
``EmailDelivery`` idempotency key covers the in-flight window and the
legacy ``EmailLog`` row covers sends from before the package adoption.
"""

from unittest.mock import patch

from community_base.mail.models import EmailDelivery
from community_base.mail.service import MailError
from django.test import TestCase

from accounts.models import User
from accounts.services.free_welcome import send_free_welcome_email
from email_app.models import EmailLog
from email_app.testing import deliver_pending_mail


class SendFreeWelcomeEmailTest(TestCase):
    def test_returns_delivery_and_writes_audit_row_after_drain(self):
        user = User.objects.create_user(email="welcome-contract@example.com")

        delivery = send_free_welcome_email(user)

        self.assertIsInstance(delivery, EmailDelivery)
        self.assertEqual(delivery.purpose, "free_welcome")
        self.assertEqual(delivery.recipient_email, user.email)
        self.assertEqual(
            delivery.idempotency_key, f"free_welcome:{user.pk}",
        )

        deliver_pending_mail()
        self.assertEqual(
            EmailLog.objects.filter(
                user=user, email_type="free_welcome",
            ).count(),
            1,
        )

    def test_second_call_returns_the_same_delivery(self):
        user = User.objects.create_user(email="welcome-twice@example.com")

        first = send_free_welcome_email(user)
        second = send_free_welcome_email(user)

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(EmailDelivery.objects.count(), 1)

    def test_accepted_send_remains_at_most_once_after_drain(self):
        user = User.objects.create_user(email="welcome-drained@example.com")
        send_free_welcome_email(user)
        deliver_pending_mail()

        again = send_free_welcome_email(user)

        self.assertIsInstance(again, EmailDelivery)
        self.assertEqual(EmailDelivery.objects.count(), 1)
        self.assertEqual(
            EmailLog.objects.filter(
                user=user, email_type="free_welcome",
            ).count(),
            1,
        )

    def test_legacy_email_log_marker_prevents_resend(self):
        user = User.objects.create_user(email="welcome-legacy@example.com")
        EmailLog.objects.create(
            user=user, email_type="free_welcome", ses_message_id="ses-legacy",
        )

        result = send_free_welcome_email(user)

        self.assertIsNone(result)
        self.assertEqual(EmailDelivery.objects.count(), 0)

    def test_unsaved_user_returns_none(self):
        self.assertIsNone(send_free_welcome_email(None))
        self.assertIsNone(send_free_welcome_email(User(email="unsaved@example.com")))
        self.assertEqual(EmailDelivery.objects.count(), 0)

    def test_local_refusal_soft_fails(self):
        user = User.objects.create_user(email="welcome-refused@example.com")

        with patch(
            "email_app.package_mail.package_send",
            side_effect=MailError("refused"),
        ):
            result = send_free_welcome_email(user)

        self.assertIsNone(result)
        self.assertEqual(EmailDelivery.objects.count(), 0)
        # The refusal must not burn the idempotency key: a later retry
        # sends normally.
        retry = send_free_welcome_email(user)
        self.assertIsInstance(retry, EmailDelivery)
