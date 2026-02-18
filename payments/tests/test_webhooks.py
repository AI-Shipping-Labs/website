"""Tests for the Stripe webhook endpoint and event handlers.

Tests cover:
- Webhook signature validation
- checkout.session.completed handling
- customer.subscription.updated handling
- customer.subscription.deleted handling
- invoice.payment_failed handling
- Idempotency (duplicate event processing)
"""

import hashlib
import hmac
import json
import time
from unittest.mock import patch

from django.test import TestCase, override_settings

from accounts.models import User
from payments.models import Tier, WebhookEvent
from payments.services import (
    handle_checkout_completed,
    handle_invoice_payment_failed,
    handle_subscription_deleted,
    handle_subscription_updated,
    is_event_already_processed,
    record_processed_event,
)


WEBHOOK_URL = "/api/webhooks/payments"
TEST_WEBHOOK_SECRET = "whsec_test_secret_key_for_testing"


def _build_stripe_signature(payload_bytes, secret=TEST_WEBHOOK_SECRET):
    """Build a valid Stripe webhook signature for testing."""
    timestamp = str(int(time.time()))
    signed_payload = f"{timestamp}.{payload_bytes.decode('utf-8')}"
    signature = hmac.new(
        secret.encode("utf-8"),
        signed_payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"t={timestamp},v1={signature}"


def _make_event_payload(event_id, event_type, data_object):
    """Build a Stripe event JSON payload."""
    return {
        "id": event_id,
        "type": event_type,
        "data": {
            "object": data_object,
        },
    }


class WebhookSignatureValidationTest(TestCase):
    """Tests that the webhook endpoint validates Stripe signatures."""

    def test_missing_signature_returns_400(self):
        """Request without Stripe-Signature header returns 400."""
        payload = json.dumps({"id": "evt_test", "type": "test"}).encode()
        response = self.client.post(
            WEBHOOK_URL,
            data=payload,
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_invalid_signature_returns_400(self):
        """Request with an invalid Stripe-Signature header returns 400."""
        payload = json.dumps({"id": "evt_test", "type": "test"}).encode()
        response = self.client.post(
            WEBHOOK_URL,
            data=payload,
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="t=123,v1=invalidsig",
        )
        self.assertEqual(response.status_code, 400)

    def test_empty_body_returns_400(self):
        """Request with empty body returns 400."""
        response = self.client.post(
            WEBHOOK_URL,
            data=b"",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="t=123,v1=invalidsig",
        )
        self.assertEqual(response.status_code, 400)

    @override_settings(STRIPE_WEBHOOK_SECRET=TEST_WEBHOOK_SECRET)
    def test_valid_signature_returns_200(self):
        """Request with a valid signature and known event type returns 200."""
        # Build a valid event that will be processed
        user = User.objects.create_user(email="sig@test.com")
        tier = Tier.objects.get(slug="basic")

        event_data = _make_event_payload(
            "evt_sig_test_1",
            "checkout.session.completed",
            {
                "id": "cs_test",
                "customer": "cus_test",
                "customer_details": {"email": "sig@test.com"},
                "subscription": "sub_test",
                "client_reference_id": str(user.pk),
                "metadata": {"tier_slug": "basic", "user_id": str(user.pk)},
            },
        )
        payload = json.dumps(event_data).encode()
        sig = _build_stripe_signature(payload)

        response = self.client.post(
            WEBHOOK_URL,
            data=payload,
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE=sig,
        )
        self.assertEqual(response.status_code, 200)

    @override_settings(STRIPE_WEBHOOK_SECRET=TEST_WEBHOOK_SECRET)
    def test_unhandled_event_type_returns_200(self):
        """Unknown event types are acknowledged with 200 but not processed."""
        event_data = _make_event_payload(
            "evt_unknown_1",
            "some.unknown.event",
            {"id": "obj_test"},
        )
        payload = json.dumps(event_data).encode()
        sig = _build_stripe_signature(payload)

        response = self.client.post(
            WEBHOOK_URL,
            data=payload,
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE=sig,
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "ignored")


class CheckoutCompletedHandlerTest(TestCase):
    """Tests for the checkout.session.completed webhook handler."""

    def test_sets_user_tier_on_checkout(self):
        """User's tier is updated to the purchased tier."""
        user = User.objects.create_user(email="checkout@test.com")
        basic_tier = Tier.objects.get(slug="basic")

        session_data = {
            "id": "cs_test_123",
            "customer": "cus_test_123",
            "customer_details": {"email": "checkout@test.com"},
            "subscription": "sub_test_123",
            "client_reference_id": str(user.pk),
            "metadata": {"tier_slug": "basic", "user_id": str(user.pk)},
        }

        handle_checkout_completed(session_data)

        user.refresh_from_db()
        self.assertEqual(user.tier, basic_tier)

    def test_stores_stripe_customer_id(self):
        """stripe_customer_id is saved on the user after checkout."""
        user = User.objects.create_user(email="cust@test.com")

        session_data = {
            "id": "cs_test_cid",
            "customer": "cus_abc123",
            "customer_details": {"email": "cust@test.com"},
            "subscription": "sub_abc123",
            "client_reference_id": str(user.pk),
            "metadata": {"tier_slug": "main", "user_id": str(user.pk)},
        }

        handle_checkout_completed(session_data)

        user.refresh_from_db()
        self.assertEqual(user.stripe_customer_id, "cus_abc123")

    def test_stores_subscription_id(self):
        """subscription_id is saved on the user after checkout."""
        user = User.objects.create_user(email="sub@test.com")

        session_data = {
            "id": "cs_test_sid",
            "customer": "cus_sub",
            "customer_details": {"email": "sub@test.com"},
            "subscription": "sub_xyz789",
            "client_reference_id": str(user.pk),
            "metadata": {"tier_slug": "premium", "user_id": str(user.pk)},
        }

        handle_checkout_completed(session_data)

        user.refresh_from_db()
        self.assertEqual(user.subscription_id, "sub_xyz789")

    def test_clears_pending_tier_on_checkout(self):
        """pending_tier is cleared after a successful checkout."""
        basic_tier = Tier.objects.get(slug="basic")
        main_tier = Tier.objects.get(slug="main")
        user = User.objects.create_user(email="pending@test.com")
        user.pending_tier = basic_tier
        user.save(update_fields=["pending_tier"])

        session_data = {
            "id": "cs_test_pending",
            "customer": "cus_pending",
            "customer_details": {"email": "pending@test.com"},
            "subscription": "sub_pending",
            "client_reference_id": str(user.pk),
            "metadata": {"tier_slug": "main", "user_id": str(user.pk)},
        }

        handle_checkout_completed(session_data)

        user.refresh_from_db()
        self.assertEqual(user.tier, main_tier)
        self.assertIsNone(user.pending_tier)

    def test_lookup_user_by_email_when_no_client_reference_id(self):
        """User is found by email when client_reference_id is not set."""
        user = User.objects.create_user(email="emailonly@test.com")

        session_data = {
            "id": "cs_test_email",
            "customer": "cus_email",
            "customer_details": {"email": "emailonly@test.com"},
            "subscription": "sub_email",
            "client_reference_id": None,
            "metadata": {"tier_slug": "basic"},
        }

        handle_checkout_completed(session_data)

        user.refresh_from_db()
        self.assertEqual(user.tier.slug, "basic")

    def test_creates_user_if_not_found(self):
        """A new user is created if no user matches the email."""
        session_data = {
            "id": "cs_test_new",
            "customer": "cus_new",
            "customer_details": {"email": "newuser@test.com"},
            "subscription": "sub_new",
            "client_reference_id": None,
            "metadata": {"tier_slug": "basic"},
        }

        handle_checkout_completed(session_data)

        user = User.objects.get(email="newuser@test.com")
        self.assertEqual(user.tier.slug, "basic")
        self.assertEqual(user.stripe_customer_id, "cus_new")
        self.assertEqual(user.subscription_id, "sub_new")

    def test_no_error_when_tier_not_found(self):
        """Handler does not crash when tier_slug is invalid."""
        user = User.objects.create_user(email="notier@test.com")

        session_data = {
            "id": "cs_test_notier",
            "customer": "cus_notier",
            "customer_details": {"email": "notier@test.com"},
            "subscription": "sub_notier",
            "client_reference_id": str(user.pk),
            "metadata": {"tier_slug": "nonexistent"},
        }

        # Should not raise
        handle_checkout_completed(session_data)

        # Tier should remain unchanged (free)
        user.refresh_from_db()
        self.assertEqual(user.tier.slug, "free")


class SubscriptionUpdatedHandlerTest(TestCase):
    """Tests for the customer.subscription.updated webhook handler."""

    def test_updates_tier_on_plan_change(self):
        """User's tier is updated when subscription plan changes."""
        basic_tier = Tier.objects.get(slug="basic")
        main_tier = Tier.objects.get(slug="main")
        main_tier.stripe_price_id_monthly = "price_main_monthly"
        main_tier.save()

        user = User.objects.create_user(email="upgrade@test.com")
        user.tier = basic_tier
        user.subscription_id = "sub_upgrade"
        user.stripe_customer_id = "cus_upgrade"
        user.save(update_fields=["tier", "subscription_id", "stripe_customer_id"])

        subscription_data = {
            "id": "sub_upgrade",
            "customer": "cus_upgrade",
            "status": "active",
            "cancel_at_period_end": False,
            "current_period_end": 1700000000,
            "items": {
                "data": [
                    {"price": {"id": "price_main_monthly"}},
                ],
            },
        }

        handle_subscription_updated(subscription_data)

        user.refresh_from_db()
        self.assertEqual(user.tier, main_tier)

    def test_updates_billing_period_end(self):
        """billing_period_end is updated from subscription data."""
        basic_tier = Tier.objects.get(slug="basic")
        basic_tier.stripe_price_id_monthly = "price_basic_monthly"
        basic_tier.save()

        user = User.objects.create_user(email="billing@test.com")
        user.tier = basic_tier
        user.subscription_id = "sub_billing"
        user.save(update_fields=["tier", "subscription_id"])

        subscription_data = {
            "id": "sub_billing",
            "customer": "cus_billing",
            "status": "active",
            "cancel_at_period_end": False,
            "current_period_end": 1700000000,
            "items": {
                "data": [
                    {"price": {"id": "price_basic_monthly"}},
                ],
            },
        }

        handle_subscription_updated(subscription_data)

        user.refresh_from_db()
        self.assertIsNotNone(user.billing_period_end)

    def test_cancel_at_period_end_does_not_change_tier(self):
        """When cancel_at_period_end is True, tier is NOT changed."""
        basic_tier = Tier.objects.get(slug="basic")
        user = User.objects.create_user(email="cancelperiod@test.com")
        user.tier = basic_tier
        user.subscription_id = "sub_cancelperiod"
        user.save(update_fields=["tier", "subscription_id"])

        subscription_data = {
            "id": "sub_cancelperiod",
            "customer": "cus_cancelperiod",
            "status": "active",
            "cancel_at_period_end": True,
            "current_period_end": 1700000000,
            "items": {
                "data": [
                    {"price": {"id": "price_free_monthly"}},
                ],
            },
        }

        handle_subscription_updated(subscription_data)

        user.refresh_from_db()
        self.assertEqual(user.tier, basic_tier)

    def test_no_error_when_user_not_found(self):
        """Handler does not crash when no user matches the subscription."""
        subscription_data = {
            "id": "sub_nobody",
            "customer": "cus_nobody",
            "status": "active",
            "cancel_at_period_end": False,
            "current_period_end": 1700000000,
            "items": {"data": [{"price": {"id": "price_test"}}]},
        }

        # Should not raise
        handle_subscription_updated(subscription_data)


class SubscriptionDeletedHandlerTest(TestCase):
    """Tests for the customer.subscription.deleted webhook handler."""

    def test_sets_tier_to_free_on_deletion(self):
        """User's tier is set to 'free' when subscription is deleted."""
        main_tier = Tier.objects.get(slug="main")
        user = User.objects.create_user(email="deleted@test.com")
        user.tier = main_tier
        user.subscription_id = "sub_deleted"
        user.stripe_customer_id = "cus_deleted"
        user.save(update_fields=["tier", "subscription_id", "stripe_customer_id"])

        subscription_data = {
            "id": "sub_deleted",
            "customer": "cus_deleted",
        }

        handle_subscription_deleted(subscription_data)

        user.refresh_from_db()
        self.assertEqual(user.tier.slug, "free")

    def test_clears_subscription_id(self):
        """subscription_id is cleared when subscription is deleted."""
        main_tier = Tier.objects.get(slug="main")
        user = User.objects.create_user(email="clearsub@test.com")
        user.tier = main_tier
        user.subscription_id = "sub_clear"
        user.save(update_fields=["tier", "subscription_id"])

        subscription_data = {
            "id": "sub_clear",
            "customer": "cus_clear",
        }

        handle_subscription_deleted(subscription_data)

        user.refresh_from_db()
        self.assertEqual(user.subscription_id, "")

    def test_clears_billing_period_end(self):
        """billing_period_end is set to None on subscription deletion."""
        from django.utils import timezone

        main_tier = Tier.objects.get(slug="main")
        user = User.objects.create_user(email="clearbilling@test.com")
        user.tier = main_tier
        user.subscription_id = "sub_clearbill"
        user.billing_period_end = timezone.now()
        user.save(update_fields=["tier", "subscription_id", "billing_period_end"])

        subscription_data = {
            "id": "sub_clearbill",
            "customer": "cus_clearbill",
        }

        handle_subscription_deleted(subscription_data)

        user.refresh_from_db()
        self.assertIsNone(user.billing_period_end)

    def test_clears_pending_tier(self):
        """pending_tier is cleared when subscription is deleted."""
        main_tier = Tier.objects.get(slug="main")
        basic_tier = Tier.objects.get(slug="basic")
        user = User.objects.create_user(email="clearpending@test.com")
        user.tier = main_tier
        user.pending_tier = basic_tier
        user.subscription_id = "sub_clearpending"
        user.save(update_fields=["tier", "pending_tier", "subscription_id"])

        subscription_data = {
            "id": "sub_clearpending",
            "customer": "cus_clearpending",
        }

        handle_subscription_deleted(subscription_data)

        user.refresh_from_db()
        self.assertIsNone(user.pending_tier)
        self.assertEqual(user.tier.slug, "free")

    def test_lookup_by_customer_id(self):
        """User is found by stripe_customer_id when subscription_id doesn't match."""
        main_tier = Tier.objects.get(slug="main")
        user = User.objects.create_user(email="bycust@test.com")
        user.tier = main_tier
        user.subscription_id = "sub_old"
        user.stripe_customer_id = "cus_bycust"
        user.save(update_fields=["tier", "subscription_id", "stripe_customer_id"])

        subscription_data = {
            "id": "sub_different",
            "customer": "cus_bycust",
        }

        handle_subscription_deleted(subscription_data)

        user.refresh_from_db()
        self.assertEqual(user.tier.slug, "free")

    def test_no_error_when_user_not_found(self):
        """Handler does not crash when no user matches the subscription."""
        subscription_data = {
            "id": "sub_ghost",
            "customer": "cus_ghost",
        }

        # Should not raise
        handle_subscription_deleted(subscription_data)


class InvoicePaymentFailedHandlerTest(TestCase):
    """Tests for the invoice.payment_failed webhook handler."""

    @patch("payments.services.send_mail")
    def test_sends_email_on_payment_failure(self, mock_send_mail):
        """An email is sent to the user when payment fails."""
        basic_tier = Tier.objects.get(slug="basic")
        user = User.objects.create_user(email="payfail@test.com")
        user.tier = basic_tier
        user.stripe_customer_id = "cus_payfail"
        user.save(update_fields=["tier", "stripe_customer_id"])

        invoice_data = {
            "customer": "cus_payfail",
            "customer_email": "payfail@test.com",
        }

        handle_invoice_payment_failed(invoice_data)

        mock_send_mail.assert_called_once()
        call_kwargs = mock_send_mail.call_args
        self.assertIn("payfail@test.com", call_kwargs[1]["recipient_list"])
        self.assertIn("Payment failed", call_kwargs[1]["subject"])

    @patch("payments.services.send_mail")
    def test_tier_not_revoked_on_payment_failure(self, mock_send_mail):
        """User's tier is NOT changed when payment fails."""
        main_tier = Tier.objects.get(slug="main")
        user = User.objects.create_user(email="keepaccess@test.com")
        user.tier = main_tier
        user.stripe_customer_id = "cus_keepaccess"
        user.save(update_fields=["tier", "stripe_customer_id"])

        invoice_data = {
            "customer": "cus_keepaccess",
            "customer_email": "keepaccess@test.com",
        }

        handle_invoice_payment_failed(invoice_data)

        user.refresh_from_db()
        self.assertEqual(user.tier, main_tier)

    @patch("payments.services.send_mail")
    def test_lookup_by_email_when_no_customer_id(self, mock_send_mail):
        """User is found by email when stripe_customer_id doesn't match."""
        user = User.objects.create_user(email="byemail@test.com")

        invoice_data = {
            "customer": "cus_unknown",
            "customer_email": "byemail@test.com",
        }

        handle_invoice_payment_failed(invoice_data)

        mock_send_mail.assert_called_once()

    @patch("payments.services.send_mail")
    def test_no_error_when_user_not_found(self, mock_send_mail):
        """Handler does not crash when no user matches."""
        invoice_data = {
            "customer": "cus_nonexistent",
            "customer_email": "nobody@test.com",
        }

        # Should not raise
        handle_invoice_payment_failed(invoice_data)
        mock_send_mail.assert_not_called()


class WebhookIdempotencyTest(TestCase):
    """Tests that webhook processing is idempotent."""

    def test_is_event_already_processed_returns_false_for_new_event(self):
        """New events are not considered already processed."""
        self.assertFalse(is_event_already_processed("evt_new_123"))

    def test_is_event_already_processed_returns_true_after_recording(self):
        """Recorded events are considered already processed."""
        record_processed_event("evt_recorded_1", "test.event")
        self.assertTrue(is_event_already_processed("evt_recorded_1"))

    def test_record_processed_event_creates_webhook_event(self):
        """record_processed_event creates a WebhookEvent record."""
        record_processed_event("evt_create_1", "checkout.session.completed", {"test": True})
        event = WebhookEvent.objects.get(stripe_event_id="evt_create_1")
        self.assertEqual(event.event_type, "checkout.session.completed")
        self.assertEqual(event.payload, {"test": True})

    def test_duplicate_event_processing_does_not_corrupt_data(self):
        """Processing the same event twice does not change the result."""
        user = User.objects.create_user(email="idempotent@test.com")
        basic_tier = Tier.objects.get(slug="basic")

        session_data = {
            "id": "cs_idemp",
            "customer": "cus_idemp",
            "customer_details": {"email": "idempotent@test.com"},
            "subscription": "sub_idemp",
            "client_reference_id": str(user.pk),
            "metadata": {"tier_slug": "basic", "user_id": str(user.pk)},
        }

        # Process first time
        handle_checkout_completed(session_data)
        user.refresh_from_db()
        self.assertEqual(user.tier, basic_tier)
        self.assertEqual(user.stripe_customer_id, "cus_idemp")

        # Process second time (should produce the same result)
        handle_checkout_completed(session_data)
        user.refresh_from_db()
        self.assertEqual(user.tier, basic_tier)
        self.assertEqual(user.stripe_customer_id, "cus_idemp")

    @override_settings(STRIPE_WEBHOOK_SECRET=TEST_WEBHOOK_SECRET)
    def test_duplicate_webhook_request_returns_already_processed(self):
        """Sending the same webhook event twice returns already_processed on second call."""
        user = User.objects.create_user(email="dupe@test.com")

        event_data = _make_event_payload(
            "evt_dupe_1",
            "checkout.session.completed",
            {
                "id": "cs_dupe",
                "customer": "cus_dupe",
                "customer_details": {"email": "dupe@test.com"},
                "subscription": "sub_dupe",
                "client_reference_id": str(user.pk),
                "metadata": {"tier_slug": "basic", "user_id": str(user.pk)},
            },
        )
        payload = json.dumps(event_data).encode()
        sig = _build_stripe_signature(payload)

        # First request
        response1 = self.client.post(
            WEBHOOK_URL,
            data=payload,
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE=sig,
        )
        self.assertEqual(response1.status_code, 200)
        self.assertEqual(response1.json()["status"], "ok")

        # Second request with same event ID
        response2 = self.client.post(
            WEBHOOK_URL,
            data=payload,
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE=sig,
        )
        self.assertEqual(response2.status_code, 200)
        self.assertEqual(response2.json()["status"], "already_processed")


class WebhookEventModelTest(TestCase):
    """Tests for the WebhookEvent model."""

    def test_str_representation(self):
        event = WebhookEvent.objects.create(
            stripe_event_id="evt_test_str",
            event_type="checkout.session.completed",
        )
        self.assertEqual(
            str(event), "checkout.session.completed (evt_test_str)"
        )

    def test_unique_stripe_event_id(self):
        """stripe_event_id must be unique."""
        from django.db import IntegrityError

        WebhookEvent.objects.create(
            stripe_event_id="evt_unique",
            event_type="test.event",
        )
        with self.assertRaises(IntegrityError):
            WebhookEvent.objects.create(
                stripe_event_id="evt_unique",
                event_type="test.event",
            )

    def test_processed_at_auto_set(self):
        event = WebhookEvent.objects.create(
            stripe_event_id="evt_auto_ts",
            event_type="test.event",
        )
        self.assertIsNotNone(event.processed_at)
