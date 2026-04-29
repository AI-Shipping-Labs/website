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

from django.core import mail
from django.test import TestCase, override_settings, tag

from accounts.models import User
from payments.exceptions import WebhookPermanentError
from payments.models import ConversionAttribution, Tier, WebhookEvent
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


class QuietSubscriptionLookupMixin:
    """Avoid real Stripe subscription lookups in webhook tests."""

    def setUp(self):
        super().setUp()
        patchers = [
            patch("payments.services._get_subscription_period_end", return_value=None),
            patch("payments.services._get_subscription_price_id", return_value=""),
            patch("payments.services._tier_from_subscription", return_value=None),
        ]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)


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


@tag('core')
class WebhookSignatureValidationTest(QuietSubscriptionLookupMixin, TestCase):
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
        Tier.objects.get(slug="basic")

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
    def test_tampered_payload_returns_400_and_no_side_effects(self):
        """Replaying a captured signature against a modified payload is rejected.

        The realistic attack: an attacker captures a valid Stripe-Signature
        header from a legitimate request, mutates the JSON body to swap
        the tier from ``basic`` to ``premium`` (or any field they control),
        and replays. Stripe's HMAC binds the signature to the exact bytes
        of the body, so the verifier must reject this and the endpoint
        must produce zero side effects: no WebhookEvent row, no tier
        change on the targeted user, and no email.
        """
        free_tier = Tier.objects.get(slug="free")
        user = User.objects.create_user(email="tampered@test.com")
        user.tier = free_tier
        user.save(update_fields=["tier"])

        # Payload A — what the attacker captured. Build a valid signature
        # for these exact bytes.
        payload_a = json.dumps(
            _make_event_payload(
                "evt_tamper_1",
                "checkout.session.completed",
                {
                    "id": "cs_tamper",
                    "customer": "cus_tamper",
                    "customer_details": {"email": "tampered@test.com"},
                    "subscription": "sub_tamper",
                    "client_reference_id": str(user.pk),
                    "metadata": {"tier_slug": "basic", "user_id": str(user.pk)},
                },
            )
        ).encode()
        sig = _build_stripe_signature(payload_a)

        # Payload B — same event id and shape, but tier_slug flipped to
        # ``premium``. The attacker is trying to upgrade the user without
        # paying. Stripe's HMAC over payload_a does NOT cover payload_b,
        # so verify_webhook_signature must raise and the endpoint must
        # return 400.
        payload_b = json.dumps(
            _make_event_payload(
                "evt_tamper_1",
                "checkout.session.completed",
                {
                    "id": "cs_tamper",
                    "customer": "cus_tamper",
                    "customer_details": {"email": "tampered@test.com"},
                    "subscription": "sub_tamper",
                    "client_reference_id": str(user.pk),
                    "metadata": {"tier_slug": "premium", "user_id": str(user.pk)},
                },
            )
        ).encode()
        # Sanity check: the tamper actually mutated the bytes, otherwise
        # the test is meaningless. (assertNotEqual on bytes is fine.)
        self.assertNotEqual(payload_a, payload_b)

        webhook_count_before = WebhookEvent.objects.count()
        outbox_len_before = len(mail.outbox)

        response = self.client.post(
            WEBHOOK_URL,
            data=payload_b,
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE=sig,
        )

        # The endpoint must reject the request.
        self.assertEqual(response.status_code, 400)
        # No WebhookEvent row was recorded — the request was rejected
        # before the idempotency layer.
        self.assertEqual(
            WebhookEvent.objects.count(),
            webhook_count_before,
            "Tampered payload must not produce a WebhookEvent row.",
        )
        # The user's tier must not have moved off ``free``.
        user.refresh_from_db()
        self.assertEqual(
            user.tier, free_tier,
            "Tampered payload must not change the user's tier.",
        )
        # No email was sent.
        self.assertEqual(
            len(mail.outbox),
            outbox_len_before,
            "Tampered payload must not send any email.",
        )

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


@tag('core')
class CheckoutCompletedHandlerTest(QuietSubscriptionLookupMixin, TestCase):
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


@tag('core')
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

    def test_cancel_at_period_end_saves_billing_period_end(self):
        """billing_period_end is saved when cancel_at_period_end is True."""

        basic_tier = Tier.objects.get(slug="basic")
        user = User.objects.create_user(email="cancel_billing@test.com")
        user.tier = basic_tier
        user.subscription_id = "sub_cancel_billing"
        user.save(update_fields=["tier", "subscription_id"])

        subscription_data = {
            "id": "sub_cancel_billing",
            "customer": "cus_cancel_billing",
            "status": "active",
            "cancel_at_period_end": True,
            "current_period_end": 1774396800,
            "items": {
                "data": [
                    {"price": {"id": "price_basic_monthly"}},
                ],
            },
        }

        handle_subscription_updated(subscription_data)

        user.refresh_from_db()
        self.assertIsNotNone(user.billing_period_end)
        self.assertEqual(user.billing_period_end.year, 2026)
        self.assertEqual(user.billing_period_end.month, 3)
        self.assertEqual(user.billing_period_end.day, 25)

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


@tag('core')
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


@tag('core')
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
        User.objects.create_user(email="byemail@test.com")

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


@tag('core')
class WebhookIdempotencyTest(QuietSubscriptionLookupMixin, TestCase):
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
        """Processing the same event twice does not change the result.

        The handler itself does not record WebhookEvent rows (that is the
        view's job), so we only need to confirm tier/customer fields stay
        stable on re-processing. The DB-side idempotency assertion lives
        in ``test_duplicate_webhook_request_returns_already_processed``,
        which exercises the full request path.
        """
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

        # Snapshot side-effect counters before any processing so we can
        # assert that re-processing produces zero net change.
        webhook_count_before = WebhookEvent.objects.count()
        outbox_len_before = len(mail.outbox)

        # Process first time
        handle_checkout_completed(session_data)
        user.refresh_from_db()
        self.assertEqual(user.tier, basic_tier)
        self.assertEqual(user.stripe_customer_id, "cus_idemp")

        # Snapshot counters AFTER the first call so we can assert the
        # second call is a true no-op (zero delta in WebhookEvent rows
        # and zero delta in mail.outbox).
        webhook_count_after_first = WebhookEvent.objects.count()
        outbox_len_after_first = len(mail.outbox)

        # Process second time (should produce the same result)
        handle_checkout_completed(session_data)
        user.refresh_from_db()
        self.assertEqual(user.tier, basic_tier)
        self.assertEqual(user.stripe_customer_id, "cus_idemp")

        # The second call must not record any extra WebhookEvent row or
        # send any extra email. A regression that double-records would
        # increase WebhookEvent.count by 1; a regression that double-
        # emails would grow mail.outbox.
        self.assertEqual(
            WebhookEvent.objects.count() - webhook_count_after_first,
            0,
            "Second handle_checkout_completed call must not create a WebhookEvent row.",
        )
        self.assertEqual(
            len(mail.outbox) - outbox_len_after_first,
            0,
            "Second handle_checkout_completed call must not send any email.",
        )
        # Sanity: confirm the handler itself didn't write WebhookEvent
        # rows on the first call either (recording is the view's job).
        self.assertEqual(
            WebhookEvent.objects.count(),
            webhook_count_before,
            "handle_checkout_completed must not create WebhookEvent rows itself.",
        )
        self.assertEqual(
            len(mail.outbox),
            outbox_len_before,
            "handle_checkout_completed must not send email on success.",
        )

    @override_settings(STRIPE_WEBHOOK_SECRET=TEST_WEBHOOK_SECRET)
    def test_duplicate_webhook_request_returns_already_processed(self):
        """Sending the same webhook event twice returns already_processed on second call.

        Also asserts the database-side invariant: regardless of how many
        times Stripe re-sends an event with the same event_id, exactly
        one WebhookEvent row exists for that id and no extra emails are
        sent. A regression that bypasses the idempotency short-circuit
        would create a second row and grow mail.outbox.
        """
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

        outbox_len_before = len(mail.outbox)

        # First request
        response1 = self.client.post(
            WEBHOOK_URL,
            data=payload,
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE=sig,
        )
        self.assertEqual(response1.status_code, 200)
        self.assertEqual(response1.json()["status"], "ok")
        # After the first POST, exactly one WebhookEvent row exists for
        # evt_dupe_1 — confirm before we re-post.
        self.assertEqual(
            WebhookEvent.objects.filter(stripe_event_id="evt_dupe_1").count(),
            1,
        )

        # Second request with same event ID
        response2 = self.client.post(
            WEBHOOK_URL,
            data=payload,
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE=sig,
        )
        self.assertEqual(response2.status_code, 200)
        self.assertEqual(response2.json()["status"], "already_processed")

        # The second POST must not create a duplicate WebhookEvent row.
        self.assertEqual(
            WebhookEvent.objects.filter(stripe_event_id="evt_dupe_1").count(),
            1,
            "Duplicate webhook POST must not create a second WebhookEvent row.",
        )
        self.assertEqual(
            ConversionAttribution.objects.filter(
                stripe_session_id="cs_dupe",
            ).count(),
            1,
            "Duplicate webhook POST must not create a second attribution row.",
        )
        # Successful checkout completion does not send mail; if it did,
        # idempotency would also have to suppress it. Either way, the
        # outbox length must not have changed across the two POSTs.
        self.assertEqual(
            len(mail.outbox),
            outbox_len_before,
            "Duplicate webhook POST must not send extra email.",
        )

    @override_settings(STRIPE_WEBHOOK_SECRET=TEST_WEBHOOK_SECRET)
    @patch("payments.services.send_mail")
    def test_duplicate_invoice_payment_failed_sends_only_one_email(
        self, mock_send_mail,
    ):
        """An invoice.payment_failed event re-delivered by Stripe must email once.

        Stripe retries failed webhook deliveries. The endpoint's
        idempotency short-circuit (``is_event_already_processed``) must
        prevent the failure-notification email from going out a second
        time, otherwise users get spammed every time Stripe retries.
        """
        # Set up a paying user so the failure handler can find them by
        # stripe_customer_id. Reuses the same shape as the existing
        # InvoicePaymentFailedHandlerTest tests.
        basic_tier = Tier.objects.get(slug="basic")
        user = User.objects.create_user(email="payfail-dupe@test.com")
        user.tier = basic_tier
        user.stripe_customer_id = "cus_payfail_dupe"
        user.save(update_fields=["tier", "stripe_customer_id"])

        event_data = _make_event_payload(
            "evt_payfail_dupe_1",
            "invoice.payment_failed",
            {
                "customer": "cus_payfail_dupe",
                "customer_email": "payfail-dupe@test.com",
            },
        )
        payload = json.dumps(event_data).encode()
        sig = _build_stripe_signature(payload)

        # First delivery — handler runs, send_mail is called once.
        response1 = self.client.post(
            WEBHOOK_URL,
            data=payload,
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE=sig,
        )
        self.assertEqual(response1.status_code, 200)
        self.assertEqual(response1.json()["status"], "ok")
        self.assertEqual(
            mock_send_mail.call_count, 1,
            "First invoice.payment_failed delivery must send exactly one email.",
        )

        # Second delivery (same event id) — short-circuited as
        # already_processed, send_mail must NOT be called again.
        response2 = self.client.post(
            WEBHOOK_URL,
            data=payload,
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE=sig,
        )
        self.assertEqual(response2.status_code, 200)
        self.assertEqual(response2.json()["status"], "already_processed")
        self.assertEqual(
            mock_send_mail.call_count, 1,
            "Duplicate invoice.payment_failed must not send a second email.",
        )


@tag('core')
@override_settings(STRIPE_WEBHOOK_SECRET=TEST_WEBHOOK_SECRET)
class WebhookHandlerFailureTest(TestCase):
    """Tests for the "only mark processed after handler succeeds" semantics.

    Regression coverage for finding #17 in the 2026-04-20 audit: a
    transient handler failure must NOT create a ``WebhookEvent`` row,
    so that Stripe's retry passes the idempotency short-circuit and
    runs the handler again.

    A permanent handler failure (``WebhookPermanentError``) must DO the
    opposite — create a terminal row and return 200 — so Stripe stops
    retrying and the on-call has a record to investigate.
    """

    def _post_checkout_event(self, event_id, user):
        """Build and POST a valid checkout.session.completed event.

        Returns the response. The caller decides what assertions to run.
        Uses a per-call payload that points at the given user with
        ``tier_slug=basic`` so happy-path runs upgrade them to basic.
        """
        event_data = _make_event_payload(
            event_id,
            "checkout.session.completed",
            {
                "id": f"cs_{event_id}",
                "customer": f"cus_{event_id}",
                "customer_details": {"email": user.email},
                # No subscription — this avoids the live Stripe API call
                # path inside the handler (_get_subscription_period_end
                # etc.) which would otherwise try to hit the network.
                "subscription": "",
                "client_reference_id": str(user.pk),
                "metadata": {"tier_slug": "basic", "user_id": str(user.pk)},
            },
        )
        payload = json.dumps(event_data).encode()
        sig = _build_stripe_signature(payload)
        return self.client.post(
            WEBHOOK_URL,
            data=payload,
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE=sig,
        )

    # ------------------------------------------------------------------
    # Scenario 1 — canonical regression: transient failure leaves no row
    # ------------------------------------------------------------------
    def test_transient_handler_failure_leaves_no_row_so_stripe_retry_succeeds(self):
        """First delivery raises generic Exception → no row, 500.

        Stripe's retry then runs the real handler, the user's tier
        updates exactly once, and exactly one ``WebhookEvent`` row exists.
        Without the fix, the second delivery would short-circuit on the
        bogus row recorded by the first failure and the user's tier
        would stay on ``free`` forever.
        """
        free_tier = Tier.objects.get(slug="free")
        basic_tier = Tier.objects.get(slug="basic")
        user = User.objects.create_user(email="retry@test.com")
        user.tier = free_tier
        user.save(update_fields=["tier"])

        event_id = "evt_retry_1"

        # Patch the EVENT_HANDLERS dispatch entry so the FIRST call
        # raises and the SECOND call runs the real handler. We use a
        # closure with a counter rather than ``side_effect=[exc, real]``
        # because ``side_effect`` as a list calls the values, not the
        # callables, on each invocation.
        call_count = {"n": 0}

        def flaky_handler(obj_dict):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("DB lock")
            return handle_checkout_completed(obj_dict)

        with patch.dict(
            "payments.views.webhooks.EVENT_HANDLERS",
            {"checkout.session.completed": flaky_handler},
        ):
            # First delivery: transient failure.
            with self.assertLogs("payments.views.webhooks", level="ERROR") as logs:
                response1 = self._post_checkout_event(event_id, user)
            self.assertEqual(response1.status_code, 500)
            self.assertIn(
                "Error processing webhook event evt_retry_1 "
                "(checkout.session.completed)",
                logs.output[0],
            )
            self.assertEqual(
                WebhookEvent.objects.filter(stripe_event_id=event_id).count(),
                0,
                "Transient handler failure must NOT create a WebhookEvent row.",
            )
            user.refresh_from_db()
            self.assertEqual(
                user.tier, free_tier,
                "Patched handler raised before doing work; tier must not have changed.",
            )

            # Second delivery: same event id, same payload — Stripe's retry.
            response2 = self._post_checkout_event(event_id, user)

        self.assertEqual(response2.status_code, 200)
        self.assertEqual(response2.json()["status"], "ok")
        self.assertEqual(
            WebhookEvent.objects.filter(
                stripe_event_id=event_id,
                status=WebhookEvent.STATUS_PROCESSED,
            ).count(),
            1,
            "Retry must record exactly one processed WebhookEvent row.",
        )
        self.assertEqual(
            WebhookEvent.objects.filter(stripe_event_id=event_id).count(),
            1,
            "Exactly one WebhookEvent row total — no duplicate from the failed attempt.",
        )
        user.refresh_from_db()
        self.assertEqual(
            user.tier, basic_tier,
            "Retry must update the user's tier to the purchased tier.",
        )
        self.assertEqual(
            call_count["n"], 2,
            "Handler must have been invoked twice: once failing, once succeeding.",
        )

    # ------------------------------------------------------------------
    # Scenario 2 — permanent failure records terminal row, Stripe stops
    # ------------------------------------------------------------------
    def test_permanent_handler_failure_records_failed_permanent_and_returns_200(self):
        """Handler raises WebhookPermanentError → row with failed_permanent, 200.

        Stripe must stop retrying. Future deliveries of the same event
        id short-circuit on the row, regardless of its status.
        """
        user = User.objects.create_user(email="perm@test.com")
        event_id = "evt_perm_1"

        call_count = {"n": 0}

        def perm_failing_handler(obj_dict):
            call_count["n"] += 1
            raise WebhookPermanentError("malformed metadata")

        with patch.dict(
            "payments.views.webhooks.EVENT_HANDLERS",
            {"checkout.session.completed": perm_failing_handler},
        ):
            # First delivery: permanent failure.
            with self.assertLogs("payments.views.webhooks", level="WARNING") as logs:
                response1 = self._post_checkout_event(event_id, user)
            self.assertEqual(
                response1.status_code, 200,
                "Permanent failure must return 200 so Stripe stops retrying.",
            )
            self.assertIn(
                "Webhook handler raised WebhookPermanentError: "
                "evt_perm_1 (checkout.session.completed): malformed metadata",
                logs.output[0],
            )
            self.assertEqual(
                WebhookEvent.objects.filter(
                    stripe_event_id=event_id,
                    status=WebhookEvent.STATUS_FAILED_PERMANENT,
                ).count(),
                1,
                "Permanent failure must record exactly one failed_permanent row.",
            )

            # Second delivery: short-circuit, handler not called again.
            response2 = self._post_checkout_event(event_id, user)

        self.assertEqual(response2.status_code, 200)
        self.assertEqual(response2.json()["status"], "already_processed")
        self.assertEqual(
            WebhookEvent.objects.filter(stripe_event_id=event_id).count(),
            1,
            "Re-delivery of a permanent-failure event must not duplicate the row.",
        )
        self.assertEqual(
            call_count["n"], 1,
            "Handler must NOT be invoked again after a permanent failure.",
        )

    def test_permanent_failure_row_carries_error_message(self):
        """The failed_permanent row stores the exception summary for debugging.

        On-call needs to see why the event was marked terminal without
        having to reach for Stripe Dashboard logs. The view truncates the
        message to 1000 chars (``repr(exc)[:1000]``) — we just confirm
        non-empty and contains the message string.
        """
        user = User.objects.create_user(email="perm_msg@test.com")
        event_id = "evt_perm_msg_1"

        def perm_failing_handler(obj_dict):
            raise WebhookPermanentError("malformed metadata: missing tier_slug")

        with patch.dict(
            "payments.views.webhooks.EVENT_HANDLERS",
            {"checkout.session.completed": perm_failing_handler},
        ):
            with self.assertLogs("payments.views.webhooks", level="WARNING") as logs:
                self._post_checkout_event(event_id, user)
        self.assertIn(
            "Webhook handler raised WebhookPermanentError: "
            "evt_perm_msg_1 (checkout.session.completed): malformed metadata",
            logs.output[0],
        )

        row = WebhookEvent.objects.get(stripe_event_id=event_id)
        self.assertEqual(row.status, WebhookEvent.STATUS_FAILED_PERMANENT)
        self.assertIn("malformed metadata", row.error_message)

    # ------------------------------------------------------------------
    # Scenario 3 — happy path still records as processed
    # ------------------------------------------------------------------
    def test_happy_path_records_status_processed(self):
        """Clean handler return → row with status=processed, 200 ok.

        Guards the default value on the new ``status`` field — a
        regression that defaulted to anything else would still create a
        row but with the wrong status.
        """
        basic_tier = Tier.objects.get(slug="basic")
        user = User.objects.create_user(email="happy@test.com")

        response = self._post_checkout_event("evt_happy_1", user)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        rows = WebhookEvent.objects.filter(stripe_event_id="evt_happy_1")
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.first().status, WebhookEvent.STATUS_PROCESSED)
        user.refresh_from_db()
        self.assertEqual(user.tier, basic_tier)

    # ------------------------------------------------------------------
    # Scenario 4 — error logs include the event id and event type
    # ------------------------------------------------------------------
    def test_transient_failure_logs_include_event_id_and_type(self):
        """On 500, on-call can find the event id + type in the log line.

        Without these, debugging a Stripe retry storm against the logs
        is nearly impossible — you have nothing to grep for.
        """
        user = User.objects.create_user(email="log@test.com")
        event_id = "evt_log_1"

        def failing_handler(obj_dict):
            raise RuntimeError("boom")

        with patch.dict(
            "payments.views.webhooks.EVENT_HANDLERS",
            {"checkout.session.completed": failing_handler},
        ):
            with self.assertLogs("payments.views.webhooks", level="ERROR") as captured:
                response = self._post_checkout_event(event_id, user)

        self.assertEqual(response.status_code, 500)
        log_blob = "\n".join(captured.output)
        self.assertIn(event_id, log_blob)
        self.assertIn("checkout.session.completed", log_blob)
