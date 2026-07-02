from django.db import IntegrityError
from django.test import TestCase

from accounts.models import User
from payments.models import PaymentAccountMismatch


class PaymentAccountMismatchModelTest(TestCase):
    def test_stripe_session_id_is_unique(self):
        paid_user = User.objects.create_user(email="paid@test.com")
        PaymentAccountMismatch.objects.create(
            stripe_session_id="cs_unique",
            stripe_customer_id="cus_unique",
            stripe_subscription_id="sub_unique",
            stripe_email="billing@test.com",
            paid_user=paid_user,
            reason=PaymentAccountMismatch.REASON_PRIMARY_EMAIL_COLLISION,
        )

        with self.assertRaises(IntegrityError):
            PaymentAccountMismatch.objects.create(
                stripe_session_id="cs_unique",
                stripe_customer_id="cus_other",
                stripe_subscription_id="sub_other",
                stripe_email="other@test.com",
                paid_user=paid_user,
                reason=PaymentAccountMismatch.REASON_ALIAS_COLLISION,
            )

    def test_mark_terminal_records_actor_note_and_timestamp(self):
        paid_user = User.objects.create_user(email="paid-terminal@test.com")
        actor = User.objects.create_user(
            email="staff-terminal@test.com",
            is_staff=True,
        )
        mismatch = PaymentAccountMismatch.objects.create(
            stripe_session_id="cs_terminal",
            stripe_customer_id="cus_terminal",
            stripe_subscription_id="sub_terminal",
            stripe_email="billing-terminal@test.com",
            paid_user=paid_user,
            reason=PaymentAccountMismatch.REASON_PRIMARY_EMAIL_COLLISION,
        )

        mismatch.mark_terminal(
            status=PaymentAccountMismatch.STATUS_RESOLVED,
            note="Merged after support review",
            actor=actor,
        )
        mismatch.save()

        mismatch.refresh_from_db()
        self.assertEqual(mismatch.status, PaymentAccountMismatch.STATUS_RESOLVED)
        self.assertEqual(mismatch.resolution_note, "Merged after support review")
        self.assertEqual(mismatch.resolved_by, actor)
        self.assertIsNotNone(mismatch.resolved_at)
