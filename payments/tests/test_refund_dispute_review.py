"""Review-first Stripe refund/dispute webhook coverage (issue #1422)."""

import hashlib
import hmac
import json
import time
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import TestCase, override_settings, tag
from django.utils import timezone

from accounts.models import TierOverride, User
from content.models import Course, CourseAccess
from payments.models import (
    MonthlyPaymentGrace,
    StripeWebhookDeliveryAttempt,
    Tier,
    WebhookEvent,
)

WEBHOOK_URL = "/api/webhooks/payments"
SECRET = "whsec_refund_dispute_test"


def _signature(payload):
    timestamp = str(int(time.time()))
    signed = f"{timestamp}.{payload.decode()}"
    digest = hmac.new(SECRET.encode(), signed.encode(), hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


def _post(client, event_id, event_type, obj, livemode=False):
    payload = json.dumps({
        "id": event_id,
        "type": event_type,
        "livemode": livemode,
        "data": {"object": obj},
    }).encode()
    return client.post(
        WEBHOOK_URL,
        data=payload,
        content_type="application/json",
        HTTP_STRIPE_SIGNATURE=_signature(payload),
    )


def _client(*, charge=None, invoice=None, charge_error=None, invoice_error=None):
    charges = Mock()
    invoices = Mock()
    if charge_error is not None:
        charges.retrieve.side_effect = charge_error
    else:
        charges.retrieve.return_value = charge
    if invoice_error is not None:
        invoices.retrieve.side_effect = invoice_error
    else:
        invoices.retrieve.return_value = invoice
    return SimpleNamespace(charges=charges, invoices=invoices)


@tag("core")
@override_settings(STRIPE_WEBHOOK_SECRET=SECRET)
class RefundDisputeReviewTest(TestCase):
    def setUp(self):
        self.free = Tier.objects.get(slug="free")
        self.main = Tier.objects.get(slug="main")
        self.premium = Tier.objects.get(slug="premium")

    def _member(self, *, email="member@test.com", sub="sub_member",
                customer="cus_member", tier=None):
        return User.objects.create_user(
            email=email,
            tier=tier or self.main,
            subscription_id=sub,
            stripe_customer_id=customer,
            tags=["active", "plan-main"],
            slack_member=True,
        )

    def _refund(self, **overrides):
        data = {
            "id": "ch_refund",
            "customer": "cus_member",
            "invoice": "in_member",
            "amount": 5000,
            "amount_refunded": 5000,
            "currency": "eur",
            "refunded": True,
        }
        data.update(overrides)
        return data

    def _invoice(self, **overrides):
        data = {
            "id": "in_member",
            "customer": "cus_member",
            "subscription": "sub_member",
        }
        data.update(overrides)
        return data

    def test_full_refund_exact_owner_is_terminal_review_and_alerts_once(self):
        user = self._member()
        client = _client(invoice=self._invoice())
        with patch(
            "payments.services.refund_dispute_review._get_stripe_client",
            return_value=client,
        ), patch("payments.services.webhook_dispatch.mail_admins") as mail:
            first = _post(
                self.client, "evt_refund_full", "charge.refunded", self._refund(),
                livemode=True,
            )
            second = _post(
                self.client, "evt_refund_full", "charge.refunded", self._refund(),
                livemode=True,
            )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["status"], "review_required")
        self.assertEqual(second.json()["status"], "already_processed")
        attempts = StripeWebhookDeliveryAttempt.objects.filter(
            stripe_event_id="evt_refund_full",
        ).order_by("attempt_number")
        self.assertEqual(
            list(attempts.values_list("outcome", flat=True)),
            ["review_required", "already_processed"],
        )
        attempt = attempts[0]
        self.assertEqual(attempt.stripe_charge_id, "ch_refund")
        self.assertEqual(attempt.stripe_invoice_id, "in_member")
        self.assertEqual(attempt.stripe_subscription_id, "sub_member")
        self.assertEqual(attempt.error_code, "full_refund")
        self.assertIn("resolution=exact_membership_owner", attempt.error_message)
        self.assertEqual(mail.call_count, 1)
        self.assertEqual(
            mail.call_args.args[0], "[Payments] Stripe refund requires review",
        )
        alert = mail.call_args.args[1]
        self.assertIn(f"Local user id: {user.pk}", alert)
        self.assertIn("No access was changed", alert)
        self.assertIn("cancel the subscription in Stripe", alert)
        self.assertEqual(client.invoices.retrieve.call_count, 1)
        self.assertTrue(WebhookEvent.objects.filter(
            stripe_event_id="evt_refund_full",
        ).exists())

    def test_partial_refund_preserves_every_entitlement_surface(self):
        user = self._member(tier=self.premium)
        user.pending_tier = self.main
        user.billing_period_end = timezone.now() + timedelta(days=25)
        user.save(update_fields=["pending_tier", "billing_period_end"])
        override = TierOverride.objects.create(
            user=user,
            original_tier=self.premium,
            override_tier=self.premium,
            expires_at=timezone.now() + timedelta(days=10),
            source="staff-review",
        )
        grace = MonthlyPaymentGrace.objects.create(
            user=user,
            base_tier_at_start=self.premium,
            stripe_customer_id="cus_member",
            stripe_subscription_id="sub_member",
            stripe_invoice_id="in_member",
            source=MonthlyPaymentGrace.SOURCE_WEBHOOK,
            status=MonthlyPaymentGrace.STATUS_ACTIVE,
            interval="month",
            interval_count=1,
            grace_started_at=timezone.now(),
            grace_expires_at=timezone.now() + timedelta(hours=168),
        )
        course = Course.objects.create(
            title="Purchased course", slug="purchased-course", status="published",
        )
        access = CourseAccess.objects.create(
            user=user, course=course, stripe_session_id="cs_course",
        )
        snapshot = {
            "tier_id": user.tier_id,
            "pending_tier_id": user.pending_tier_id,
            "billing_period_end": user.billing_period_end,
            "subscription_id": user.subscription_id,
            "stripe_customer_id": user.stripe_customer_id,
            "tags": list(user.tags),
            "slack_member": user.slack_member,
        }
        client = _client(invoice=self._invoice())
        with patch(
            "payments.services.refund_dispute_review._get_stripe_client",
            return_value=client,
        ), patch("payments.services.webhook_dispatch.mail_admins"):
            response = _post(
                self.client,
                "evt_refund_partial",
                "charge.refunded",
                self._refund(amount_refunded=1200, refunded=False),
            )

        self.assertEqual(response.json()["status"], "review_required")
        attempt = StripeWebhookDeliveryAttempt.objects.get(
            stripe_event_id="evt_refund_partial",
        )
        self.assertEqual(attempt.error_code, "partial_refund")
        self.assertIn("amount=1200", attempt.error_message)
        user.refresh_from_db()
        self.assertEqual(
            {
                "tier_id": user.tier_id,
                "pending_tier_id": user.pending_tier_id,
                "billing_period_end": user.billing_period_end,
                "subscription_id": user.subscription_id,
                "stripe_customer_id": user.stripe_customer_id,
                "tags": list(user.tags),
                "slack_member": user.slack_member,
            },
            snapshot,
        )
        override.refresh_from_db()
        grace.refresh_from_db()
        access.refresh_from_db()
        self.assertTrue(override.is_active)
        self.assertEqual(grace.status, MonthlyPaymentGrace.STATUS_ACTIVE)
        self.assertEqual(access.stripe_session_id, "cs_course")

    def test_non_membership_refund_is_reviewed_without_owner_guess(self):
        unrelated = self._member()
        charge = self._refund(invoice=None, customer="cus_unrelated_charge")
        client = _client()
        with patch(
            "payments.services.refund_dispute_review._get_stripe_client",
            return_value=client,
        ), patch("payments.services.webhook_dispatch.mail_admins") as mail:
            response = _post(
                self.client, "evt_one_off", "charge.refunded", charge,
            )
        self.assertEqual(response.json()["status"], "review_required")
        attempt = StripeWebhookDeliveryAttempt.objects.get(
            stripe_event_id="evt_one_off",
        )
        self.assertIn("resolution=non_membership_charge", attempt.error_message)
        client.invoices.retrieve.assert_not_called()
        unrelated.refresh_from_db()
        self.assertEqual(unrelated.tier, self.main)
        self.assertNotIn("Local user email:", mail.call_args.args[1])

    def test_dispute_created_follows_charge_and_invoice_to_exact_owner(self):
        self._member()
        charge = {
            "id": "ch_disputed",
            "invoice": "in_member",
            "customer": "cus_member",
        }
        client = _client(charge=charge, invoice=self._invoice())
        dispute = {
            "id": "dp_open",
            "charge": "ch_disputed",
            "status": "needs_response",
            "amount": 5000,
            "currency": "eur",
        }
        with patch(
            "payments.services.refund_dispute_review._get_stripe_client",
            return_value=client,
        ), patch("payments.services.webhook_dispatch.mail_admins") as mail:
            response = _post(
                self.client, "evt_dispute_open", "charge.dispute.created", dispute,
            )
        self.assertEqual(response.json()["status"], "review_required")
        attempt = StripeWebhookDeliveryAttempt.objects.get(
            stripe_event_id="evt_dispute_open",
        )
        self.assertEqual(attempt.stripe_dispute_id, "dp_open")
        self.assertEqual(attempt.stripe_charge_id, "ch_disputed")
        self.assertEqual(attempt.stripe_invoice_id, "in_member")
        self.assertEqual(attempt.error_code, "dispute_created")
        self.assertIn("dispute_status=needs_response", attempt.error_message)
        self.assertEqual(
            mail.call_args.args[0], "[Payments] Stripe dispute requires review",
        )

    def test_closed_disputes_record_exact_won_or_lost_outcome(self):
        self._member()
        charge = {
            "id": "ch_disputed",
            "invoice": "in_member",
            "customer": "cus_member",
        }
        for status in ("won", "lost"):
            with self.subTest(status=status):
                client = _client(charge=charge, invoice=self._invoice())
                dispute = {
                    "id": f"dp_{status}",
                    "charge": "ch_disputed",
                    "status": status,
                    "amount": 5000,
                    "currency": "eur",
                }
                with patch(
                    "payments.services.refund_dispute_review._get_stripe_client",
                    return_value=client,
                ), patch("payments.services.webhook_dispatch.mail_admins"):
                    response = _post(
                        self.client,
                        f"evt_dispute_{status}",
                        "charge.dispute.closed",
                        dispute,
                    )
                self.assertEqual(response.json()["status"], "review_required")
                attempt = StripeWebhookDeliveryAttempt.objects.get(
                    stripe_event_id=f"evt_dispute_{status}",
                )
                self.assertEqual(attempt.error_code, f"dispute_closed_{status}")

    def test_unmatched_and_ambiguous_owner_are_terminal_reviews(self):
        for email in ("dup1@test.com", "dup2@test.com"):
            self._member(email=email, sub="", customer="cus_duplicate")
        cases = [
            ("unmatched", "sub_missing", "cus_missing", "unmatched_local_owner"),
            ("ambiguous", "sub_new", "cus_duplicate", "ambiguous_local_owner"),
        ]
        for label, sub, customer, expected in cases:
            with self.subTest(label=label):
                invoice = self._invoice(subscription=sub, customer=customer)
                charge = self._refund(customer=customer)
                client = _client(invoice=invoice)
                with patch(
                    "payments.services.refund_dispute_review._get_stripe_client",
                    return_value=client,
                ), patch("payments.services.webhook_dispatch.mail_admins"):
                    response = _post(
                        self.client, f"evt_{label}", "charge.refunded", charge,
                    )
                self.assertEqual(response.status_code, 200)
                attempt = StripeWebhookDeliveryAttempt.objects.get(
                    stripe_event_id=f"evt_{label}",
                )
                self.assertIn(f"resolution={expected}", attempt.error_message)
                self.assertTrue(WebhookEvent.objects.filter(
                    stripe_event_id=f"evt_{label}",
                ).exists())

    def test_transient_invoice_failure_retries_without_terminal_evidence(self):
        self._member()
        failed = _client(invoice_error=RuntimeError("temporary Stripe outage"))
        with patch(
            "payments.services.refund_dispute_review._get_stripe_client",
            return_value=failed,
        ), patch("payments.services.webhook_dispatch.mail_admins") as mail:
            first = _post(
                self.client, "evt_retry_review", "charge.refunded", self._refund(),
            )
        self.assertEqual(first.status_code, 500)
        first_attempt = StripeWebhookDeliveryAttempt.objects.get(
            stripe_event_id="evt_retry_review",
        )
        self.assertEqual(first_attempt.outcome, "failed_transient")
        self.assertEqual(first_attempt.stripe_invoice_id, "in_member")
        self.assertFalse(WebhookEvent.objects.filter(
            stripe_event_id="evt_retry_review",
        ).exists())
        mail.assert_not_called()

        recovered = _client(invoice=self._invoice())
        with patch(
            "payments.services.refund_dispute_review._get_stripe_client",
            return_value=recovered,
        ), patch("payments.services.webhook_dispatch.mail_admins") as mail:
            second = _post(
                self.client, "evt_retry_review", "charge.refunded", self._refund(),
            )
        self.assertEqual(second.json()["status"], "review_required")
        self.assertEqual(mail.call_count, 1)
        self.assertEqual(
            list(StripeWebhookDeliveryAttempt.objects.filter(
                stripe_event_id="evt_retry_review",
            ).order_by("attempt_number").values_list("attempt_number", flat=True)),
            [1, 2],
        )

    def test_malformed_snapshot_is_terminal_without_stripe_or_pii_persistence(self):
        client = Mock()
        event = {
            "id": "not-a-charge",
            "customer": "victim@example.com",
            "invoice": {"id": "in_" + "x" * 300},
            "amount": "secret-card-4242",
            "amount_refunded": 12,
            "currency": "",
            "refunded": True,
            "receipt_url": "https://secret.example/receipt",
            "payment_method": {"card": {"last4": "4242"}},
        }
        with patch(
            "payments.services.refund_dispute_review._get_stripe_client",
            return_value=client,
        ), patch("payments.services.webhook_dispatch.mail_admins"):
            response = _post(
                self.client, "evt_malformed_review", "charge.refunded", event,
            )
        self.assertEqual(response.status_code, 200)
        attempt = StripeWebhookDeliveryAttempt.objects.get(
            stripe_event_id="evt_malformed_review",
        )
        self.assertEqual(attempt.outcome, "review_required")
        self.assertEqual(attempt.error_code, "malformed_refund")
        client.assert_not_called()
        persisted = " ".join(
            str(getattr(attempt, field.name, ""))
            for field in attempt._meta.fields
            if field.name != "requested_by"
        )
        for forbidden in (
            "victim@example.com", "4242", "receipt", "secret.example",
        ):
            self.assertNotIn(forbidden, persisted)

    def test_unsafe_dispute_ids_and_status_are_quarantined_from_evidence_and_alert(self):
        client = Mock()
        dispute = {
            "id": "dp_sensitive@example.test",
            "charge": "ch_sensitive@example.test",
            "status": "private-note@example.test",
            "amount": 5000,
            "currency": "eur",
        }
        with patch(
            "payments.services.refund_dispute_review._get_stripe_client",
            return_value=client,
        ), patch("payments.services.webhook_dispatch.mail_admins") as mail:
            response = _post(
                self.client,
                "evt_unsafe_dispute_snapshot",
                "charge.dispute.created",
                dispute,
            )

        self.assertEqual(response.status_code, 200)
        attempt = StripeWebhookDeliveryAttempt.objects.get(
            stripe_event_id="evt_unsafe_dispute_snapshot",
        )
        self.assertEqual(attempt.outcome, "review_required")
        self.assertEqual(attempt.error_code, "malformed_dispute")
        self.assertEqual(attempt.stripe_charge_id, "")
        self.assertEqual(attempt.stripe_dispute_id, "")
        client.assert_not_called()
        alert = mail.call_args.args[1]
        for forbidden in (
            "sensitive@example.test",
            "private-note@example.test",
        ):
            self.assertNotIn(forbidden, attempt.error_message)
            self.assertNotIn(forbidden, alert)

    def test_unsafe_retrieved_ids_are_quarantined_from_evidence_and_alert(self):
        charge = {
            "id": "ch_disputed",
            "invoice": "in_private@example.test",
            "customer": "cus_private@example.test",
        }
        client = _client(charge=charge)
        dispute = {
            "id": "dp_open",
            "charge": "ch_disputed",
            "status": "needs_response",
            "amount": 5000,
            "currency": "eur",
        }
        with patch(
            "payments.services.refund_dispute_review._get_stripe_client",
            return_value=client,
        ), patch("payments.services.webhook_dispatch.mail_admins") as mail:
            response = _post(
                self.client,
                "evt_unsafe_dispute_retrieval",
                "charge.dispute.created",
                dispute,
            )

        self.assertEqual(response.status_code, 200)
        attempt = StripeWebhookDeliveryAttempt.objects.get(
            stripe_event_id="evt_unsafe_dispute_retrieval",
        )
        self.assertEqual(attempt.outcome, "review_required")
        self.assertEqual(attempt.stripe_charge_id, "ch_disputed")
        self.assertEqual(attempt.stripe_dispute_id, "dp_open")
        self.assertEqual(attempt.stripe_invoice_id, "")
        self.assertEqual(attempt.stripe_customer_id, "")
        client.invoices.retrieve.assert_not_called()
        alert = mail.call_args.args[1]
        for forbidden in (
            "in_private@example.test",
            "cus_private@example.test",
        ):
            self.assertNotIn(forbidden, attempt.error_message)
            self.assertNotIn(forbidden, alert)

    def test_unhandled_dispute_update_remains_outside_contract(self):
        response = _post(
            self.client,
            "evt_dispute_update",
            "charge.dispute.updated",
            {"id": "dp_update", "charge": "ch_update"},
        )
        self.assertEqual(response.json()["status"], "ignored")
        self.assertFalse(StripeWebhookDeliveryAttempt.objects.exists())
