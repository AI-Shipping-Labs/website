"""Delayed-notification Checkout settlement coverage for issue #1423."""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings, tag
from django.utils import timezone

from accounts.models import User
from content.models import Course, CourseAccess
from email_app.models import EmailLog
from email_app.services import EmailServiceError
from payments.models import (
    CheckoutAccountBinding,
    CheckoutFulfillment,
    PaymentAccountMismatch,
    StripeWebhookDeliveryAttempt,
    WebhookEvent,
)
from payments.services import (
    handle_checkout_async_payment_failed,
    handle_checkout_async_payment_succeeded,
    handle_checkout_completed,
)
from payments.services.stripe_endpoint_verifier import REQUIRED_EVENTS
from payments.services.webhook_dispatch import EVENT_HANDLERS, process_event
from tests.fixtures import TierSetupMixin


@tag("core")
@override_settings(STRIPE_SECRET_KEY="sk_test_delayed")
class DelayedCheckoutTest(TierSetupMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.basic_tier.stripe_price_id_monthly = "price_basic_m"
        cls.basic_tier.save(update_fields=["stripe_price_id_monthly"])

    def setUp(self):
        self.user = User.objects.create_user(email="delayed@test.com")
        self.binding, self.reference = CheckoutAccountBinding.issue(
            user=self.user,
            tier=self.basic_tier,
            billing_period=CheckoutAccountBinding.PERIOD_MONTHLY,
            expires_at=timezone.now() + timedelta(minutes=30),
        )
        self.patchers = [
            patch(
                "payments.services.webhook_handlers._bound_checkout_price_id",
                return_value="price_basic_m",
            ),
            patch("payments.services._get_subscription_price_id", return_value="price_basic_m"),
            patch("payments.services._get_subscription_period_end", return_value=None),
            patch("payments.services._record_conversion_attribution"),
            patch("payments.services._community_invite"),
            patch("payments.services.send_mail"),
            patch("community.services.staff_notifications.notify_paid_signup"),
        ]
        for patcher in self.patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def membership_session(self, *, payment_status="unpaid", session_id="cs_delayed"):
        return {
            "id": session_id,
            "customer": "cus_delayed",
            "customer_details": {"email": self.user.email},
            "subscription": "sub_delayed",
            "client_reference_id": self.reference,
            "metadata": {"tier_slug": "basic"},
            "payment_status": payment_status,
            "status": "complete",
            "livemode": False,
            "created": int(timezone.now().timestamp()),
        }

    def test_unpaid_completed_reserves_without_any_entitlement(self):
        handle_checkout_completed(
            self.membership_session(),
            event_context={"event_id": "evt_pending"},
        )

        self.user.refresh_from_db()
        row = CheckoutFulfillment.objects.get(stripe_session_id="cs_delayed")
        self.assertEqual(row.status, CheckoutFulfillment.STATUS_AWAITING_PAYMENT)
        self.assertEqual(row.binding, self.binding)
        self.assertEqual(row.user, self.user)
        self.assertEqual(row.tier, self.basic_tier)
        self.assertEqual(self.user.tier, self.free_tier)
        self.assertFalse(PaymentAccountMismatch.objects.exists())
        self.assertEqual(row.details["purchase_kind"], "tier")
        self.assertNotIn(self.user.email, str(row.details))

    def test_paid_async_success_uses_reserved_binding_after_expiry(self):
        pending = self.membership_session()
        handle_checkout_completed(pending)
        CheckoutAccountBinding.objects.filter(pk=self.binding.pk).update(
            expires_at=timezone.now() - timedelta(days=2),
        )

        paid = dict(pending, payment_status="paid")
        handle_checkout_async_payment_succeeded(
            paid,
            event_context={"event_id": "evt_success", "created": paid["created"]},
        )

        self.user.refresh_from_db()
        row = CheckoutFulfillment.objects.get(stripe_session_id="cs_delayed")
        self.assertEqual(row.status, CheckoutFulfillment.STATUS_FULFILLED)
        self.assertEqual(self.user.tier, self.basic_tier)

    def test_out_of_order_success_requires_session_created_inside_binding_window(self):
        original_created = timezone.now() - timedelta(days=2)
        CheckoutAccountBinding.objects.filter(pk=self.binding.pk).update(
            created_at=original_created,
            expires_at=original_created + timedelta(minutes=30),
        )
        self.binding.refresh_from_db()
        inside = int((self.binding.created_at + timedelta(seconds=1)).timestamp())
        payload = self.membership_session(payment_status="paid", session_id="cs_out_order")
        payload["created"] = inside

        handle_checkout_async_payment_succeeded(
            payload,
            event_context={"event_id": "evt_ooo", "created": inside},
        )
        self.assertEqual(
            CheckoutFulfillment.objects.get(stripe_session_id="cs_out_order").status,
            CheckoutFulfillment.STATUS_FULFILLED,
        )

    def test_missing_session_created_fails_closed_for_expired_binding(self):
        CheckoutAccountBinding.objects.filter(pk=self.binding.pk).update(
            expires_at=timezone.now() - timedelta(minutes=1),
        )
        payload = self.membership_session(payment_status="paid", session_id="cs_no_created")
        payload.pop("created")
        handle_checkout_async_payment_succeeded(payload, event_context={"event_id": "evt_bad"})
        row = CheckoutFulfillment.objects.get(stripe_session_id="cs_no_created")
        self.assertEqual(row.status, CheckoutFulfillment.STATUS_QUARANTINED)
        self.assertEqual(row.reason, PaymentAccountMismatch.REASON_EXPIRED_BINDING)

    @patch(
        "email_app.services.email_service.EmailService._send_ses",
        return_value="ses-delayed-failure",
    )
    def test_failure_records_once_and_deduplicates_transactional_email(self, _mock_ses):
        pending = self.membership_session()
        handle_checkout_completed(pending)
        handle_checkout_async_payment_failed(
            pending,
            event_context={"event_id": "evt_failed_a", "created": pending["created"]},
        )
        handle_checkout_async_payment_failed(
            pending,
            event_context={"event_id": "evt_failed_b", "created": pending["created"]},
        )

        row = CheckoutFulfillment.objects.get(stripe_session_id="cs_delayed")
        self.user.refresh_from_db()
        self.assertEqual(row.status, CheckoutFulfillment.STATUS_PAYMENT_FAILED)
        self.assertEqual(self.user.tier, self.free_tier)
        logs = EmailLog.objects.filter(email_type="checkout_payment_failed")
        self.assertEqual(logs.count(), 1)
        self.assertEqual(
            logs.get().subject,
            "Your AI Shipping Labs payment didn't complete",
        )

    @patch("payments.services.webhook_handlers._send_checkout_failure_email")
    def test_wrong_mode_failure_is_quarantined_without_email(self, send_failure):
        payload = self.membership_session()
        payload["livemode"] = True

        handle_checkout_async_payment_failed(payload)

        row = CheckoutFulfillment.objects.get(stripe_session_id="cs_delayed")
        self.assertEqual(row.status, CheckoutFulfillment.STATUS_QUARANTINED)
        self.assertEqual(
            row.reason,
            PaymentAccountMismatch.REASON_STRIPE_MODE_MISMATCH,
        )
        send_failure.assert_not_called()

    @patch("payments.services.webhook_handlers._send_checkout_failure_email")
    def test_incomplete_failure_is_quarantined_without_email(self, send_failure):
        payload = self.membership_session()
        payload["status"] = "open"

        handle_checkout_async_payment_failed(payload)

        row = CheckoutFulfillment.objects.get(stripe_session_id="cs_delayed")
        self.assertEqual(row.status, CheckoutFulfillment.STATUS_QUARANTINED)
        self.assertEqual(
            row.reason,
            PaymentAccountMismatch.REASON_INCOMPLETE_CHECKOUT,
        )
        send_failure.assert_not_called()

    @patch("payments.services.webhook_handlers._send_checkout_failure_email")
    def test_wrong_membership_price_failure_is_quarantined_without_email(
        self,
        send_failure,
    ):
        with patch(
            "payments.services.webhook_handlers._bound_checkout_price_id",
            return_value="price_wrong",
        ):
            handle_checkout_async_payment_failed(self.membership_session())

        row = CheckoutFulfillment.objects.get(stripe_session_id="cs_delayed")
        self.assertEqual(row.status, CheckoutFulfillment.STATUS_QUARANTINED)
        self.assertEqual(row.reason, PaymentAccountMismatch.REASON_TIER_MISMATCH)
        send_failure.assert_not_called()

    @patch("payments.services.webhook_handlers._send_checkout_failure_email")
    def test_existing_quarantine_is_terminal_against_failure(self, send_failure):
        payload = self.membership_session()
        payload["livemode"] = True
        handle_checkout_completed(payload)
        before = CheckoutFulfillment.objects.get(stripe_session_id="cs_delayed")
        self.assertEqual(before.status, CheckoutFulfillment.STATUS_QUARANTINED)

        handle_checkout_async_payment_failed(dict(payload, livemode=False))

        after = CheckoutFulfillment.objects.get(stripe_session_id="cs_delayed")
        self.assertEqual(after.status, CheckoutFulfillment.STATUS_QUARANTINED)
        self.assertEqual(after.reason, before.reason)
        self.assertEqual(after.details, before.details)
        send_failure.assert_not_called()

    @patch("email_app.services.email_service.EmailService._send_ses")
    def test_unknown_failure_never_uses_payload_email_as_authority(self, mock_ses):
        payload = self.membership_session(session_id="cs_unknown")
        payload["client_reference_id"] = None
        payload["customer_details"] = {"email": "payload-only@test.com"}
        handle_checkout_async_payment_failed(payload, event_context={"event_id": "evt_unknown"})
        row = CheckoutFulfillment.objects.get(stripe_session_id="cs_unknown")
        self.assertEqual(row.status, CheckoutFulfillment.STATUS_PAYMENT_FAILED)
        self.assertIsNone(row.user)
        self.assertFalse(User.objects.filter(email="payload-only@test.com").exists())
        self.assertFalse(EmailLog.objects.filter(email_type="checkout_payment_failed").exists())
        mock_ses.assert_not_called()

    @patch("email_app.services.email_service.EmailService._send_ses")
    def test_failure_email_transport_error_keeps_state_and_retries(self, mock_ses):
        mock_ses.side_effect = [EmailServiceError("SES unavailable"), "ses-retried"]
        payload = self.membership_session()
        handle_checkout_completed(payload)
        with self.assertRaises(EmailServiceError):
            handle_checkout_async_payment_failed(payload)
        self.assertEqual(
            CheckoutFulfillment.objects.get(stripe_session_id="cs_delayed").status,
            CheckoutFulfillment.STATUS_PAYMENT_FAILED,
        )
        self.assertFalse(EmailLog.objects.filter(email_type="checkout_payment_failed").exists())

        handle_checkout_async_payment_failed(payload)
        self.assertEqual(
            EmailLog.objects.filter(email_type="checkout_payment_failed").count(),
            1,
        )

    @patch(
        "email_app.services.email_service.EmailService._send_ses",
        return_value="ses-recovery",
    )
    def test_paid_success_recovers_payment_failed_once(self, _mock_ses):
        payload = self.membership_session()
        handle_checkout_completed(payload)
        handle_checkout_async_payment_failed(payload)
        handle_checkout_async_payment_succeeded(dict(payload, payment_status="paid"))
        handle_checkout_async_payment_failed(payload)
        self.assertEqual(
            CheckoutFulfillment.objects.get(stripe_session_id="cs_delayed").status,
            CheckoutFulfillment.STATUS_FULFILLED,
        )


@tag("core")
@override_settings(STRIPE_SECRET_KEY="sk_test_delayed")
class DelayedCourseCheckoutTest(TierSetupMixin, TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="course-delayed@test.com")
        self.course = Course.objects.create(
            title="Delayed Course",
            slug="delayed-course",
            status="published",
            required_level=20,
            individual_price_eur=Decimal("49.00"),
            stripe_price_id="price_course",
        )
        self.payload = {
            "id": "cs_course_delayed",
            "customer": "cus_course",
            "customer_details": {"email": self.user.email},
            "subscription": "",
            "client_reference_id": str(self.user.pk),
            "metadata": {"user_id": str(self.user.pk), "course_id": str(self.course.pk)},
            "payment_status": "unpaid",
            "status": "complete",
            "livemode": False,
            "line_items": {"data": [{"price": {"id": "price_course"}}]},
        }
        self.patches = [
            patch("payments.services._record_conversion_attribution"),
            patch("payments.services.send_mail"),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def test_course_access_waits_for_paid_async_success(self):
        with patch(
            "payments.services.webhook_handlers._legacy_numeric_reference_allowed",
            return_value=True,
        ):
            handle_checkout_completed(self.payload)
            self.assertFalse(CourseAccess.objects.filter(user=self.user, course=self.course).exists())
            handle_checkout_async_payment_succeeded(dict(self.payload, payment_status="paid"))
        access = CourseAccess.objects.get(user=self.user, course=self.course)
        self.assertEqual(access.stripe_session_id, "cs_course_delayed")
        self.assertEqual(
            CheckoutFulfillment.objects.get(stripe_session_id="cs_course_delayed").status,
            CheckoutFulfillment.STATUS_FULFILLED,
        )

    @patch("payments.services.webhook_handlers._send_checkout_failure_email")
    def test_wrong_course_price_failure_is_quarantined_without_email(
        self,
        send_failure,
    ):
        payload = dict(self.payload)
        payload["line_items"] = {"data": [{"price": {"id": "price_wrong"}}]}

        handle_checkout_async_payment_failed(payload)

        row = CheckoutFulfillment.objects.get(
            stripe_session_id="cs_course_delayed",
        )
        self.assertEqual(row.status, CheckoutFulfillment.STATUS_QUARANTINED)
        self.assertEqual(row.reason, PaymentAccountMismatch.REASON_TIER_MISMATCH)
        self.assertFalse(
            CourseAccess.objects.filter(user=self.user, course=self.course).exists(),
        )
        send_failure.assert_not_called()

    @patch("payments.services.webhook_handlers._send_checkout_failure_email")
    def test_valid_course_failure_uses_course_retry_destination(self, send_failure):
        handle_checkout_async_payment_failed(self.payload)

        row = CheckoutFulfillment.objects.get(
            stripe_session_id="cs_course_delayed",
        )
        self.assertEqual(row.status, CheckoutFulfillment.STATUS_PAYMENT_FAILED)
        self.assertEqual(row.user, self.user)
        self.assertFalse(
            CourseAccess.objects.filter(user=self.user, course=self.course).exists(),
        )
        send_failure.assert_called_once_with(
            user=self.user,
            session_id="cs_course_delayed",
            course=self.course,
        )


@tag("core")
class DelayedCheckoutDispatchContractTest(TestCase):
    def test_dispatcher_and_verifier_include_exact_eleven_events(self):
        expected = {
            "checkout.session.completed",
            "checkout.session.async_payment_succeeded",
            "checkout.session.async_payment_failed",
            "customer.subscription.updated",
            "customer.subscription.deleted",
            "invoice.payment_failed",
            "invoice.paid",
            "customer.updated",
            "charge.refunded",
            "charge.dispute.created",
            "charge.dispute.closed",
        }
        self.assertEqual(set(REQUIRED_EVENTS), expected)
        self.assertEqual(set(EVENT_HANDLERS), expected)

    def test_async_attempt_and_terminal_event_are_recorded(self):
        with patch(
            "payments.services.webhook_dispatch.EVENT_HANDLERS",
            {"checkout.session.async_payment_failed": lambda obj, event_context=None: None},
        ):
            outcome, status = process_event(
                event_id="evt_async_contract",
                event_type="checkout.session.async_payment_failed",
                obj={"id": "cs_contract", "customer": "cus_contract"},
                livemode=False,
                event_created=123,
            )
        self.assertEqual((outcome, status), ("processed", 200))
        attempt = StripeWebhookDeliveryAttempt.objects.get(
            stripe_event_id="evt_async_contract",
        )
        self.assertEqual(attempt.stripe_object_id, "cs_contract")
        self.assertTrue(WebhookEvent.objects.filter(stripe_event_id="evt_async_contract").exists())
