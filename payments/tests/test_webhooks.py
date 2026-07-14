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
from datetime import datetime as _dt
from datetime import timezone as _tz
from smtplib import SMTPException
from unittest.mock import MagicMock, patch

from django.core import mail
from django.test import TestCase, override_settings, tag

from accounts.models import EmailAlias, User
from community.models import CommunityAuditLog
from payments import services as payment_services
from payments.exceptions import WebhookPermanentError
from payments.models import (
    ConversionAttribution,
    PaymentAccountMismatch,
    Tier,
    WebhookEvent,
)
from payments.services import (
    handle_checkout_completed as _handle_checkout_completed,
)
from payments.services import (
    handle_invoice_payment_failed,
    handle_subscription_deleted,
    handle_subscription_updated,
    is_event_already_processed,
    record_processed_event,
)

WEBHOOK_URL = "/api/webhooks/payments"
TEST_WEBHOOK_SECRET = "whsec_test_secret_key_for_testing"


def _completed_session(data):
    result = {
        "payment_status": "paid",
        "status": "complete",
        "livemode": str(payment_services.get_config("STRIPE_SECRET_KEY", "")).startswith(
            ("sk_live_", "rk_live_")
        ),
    }
    result.update(data)
    return result


def handle_checkout_completed(session_data):
    return _handle_checkout_completed(_completed_session(session_data))


class QuietSubscriptionLookupMixin:
    """Avoid real Stripe subscription lookups in webhook tests."""

    def setUp(self):
        super().setUp()
        # The 3-step resolver fallback in ``handle_checkout_completed``
        # (issue #663) reaches Stripe via ``_get_stripe_client``. Tests
        # that don't exercise the resolver still need an authoritative
        # successful response rather than a fake outage: outages now
        # deliberately propagate so Stripe can retry (#1105).
        quiet_client_object = MagicMock()
        quiet_client_object.subscriptions.retrieve.return_value = {
            "items": {"data": [{"price": {
                "id": "price_test_unmapped",
                "metadata": {},
                "unit_amount": 99999,
                "recurring": {"interval": "month"},
            }}]},
        }
        quiet_client = patch(
            "payments.services._get_stripe_client",
            return_value=quiet_client_object,
        )
        patchers = [
            patch("payments.services._get_subscription_period_end", return_value=None),
            patch("payments.services._get_subscription_price_id", return_value=""),
            patch("payments.services._tier_from_subscription", return_value=None),
            quiet_client,
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
    if event_type == "checkout.session.completed":
        data_object = _completed_session(data_object)
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
    def test_checkout_completed_webhook_fulfills_payment_link_purchase(self):
        """Payment Link checkout webhook still updates access and records idempotency."""
        user = User.objects.create_user(email="payment-link-webhook@test.com")
        basic_tier = Tier.objects.get(slug="basic")

        event_data = _make_event_payload(
            "evt_payment_link_checkout_1",
            "checkout.session.completed",
            {
                "id": "cs_payment_link_1",
                "customer": "cus_payment_link",
                "customer_details": {"email": "payment-link-webhook@test.com"},
                "subscription": "sub_payment_link",
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
        self.assertEqual(response.json()["status"], "ok")
        user.refresh_from_db()
        self.assertEqual(user.tier, basic_tier)
        self.assertEqual(user.stripe_customer_id, "cus_payment_link")
        self.assertEqual(user.subscription_id, "sub_payment_link")
        self.assertEqual(
            WebhookEvent.objects.filter(
                stripe_event_id="evt_payment_link_checkout_1",
                event_type="checkout.session.completed",
            ).count(),
            1,
        )
        self.assertEqual(
            ConversionAttribution.objects.filter(
                stripe_session_id="cs_payment_link_1",
                user=user,
                tier=basic_tier,
                stripe_subscription_id="sub_payment_link",
            ).count(),
            1,
        )

        duplicate_response = self.client.post(
            WEBHOOK_URL,
            data=payload,
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE=sig,
        )

        self.assertEqual(duplicate_response.status_code, 200)
        self.assertEqual(duplicate_response.json()["status"], "already_processed")
        self.assertEqual(
            WebhookEvent.objects.filter(
                stripe_event_id="evt_payment_link_checkout_1",
            ).count(),
            1,
        )
        self.assertEqual(
            ConversionAttribution.objects.filter(
                stripe_session_id="cs_payment_link_1",
            ).count(),
            1,
        )

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

    def test_legacy_reference_with_different_email_is_quarantined(self):
        user = User.objects.create_user(email="member@test.com")

        session_data = {
            "id": "cs_alias_1105",
            "customer": "cus_alias_1105",
            "customer_details": {"email": "Billing+Stripe@Test.com"},
            "subscription": "sub_alias_1105",
            "client_reference_id": str(user.pk),
            "metadata": {"tier_slug": "premium"},
        }

        handle_checkout_completed(session_data)
        handle_checkout_completed(session_data)

        user.refresh_from_db()
        self.assertEqual(user.tier.slug, "free")
        self.assertEqual(user.stripe_customer_id, "")
        self.assertFalse(
            User.objects.filter(email__iexact="billing+stripe@test.com")
            .exclude(pk=user.pk)
            .exists()
        )
        self.assertFalse(EmailAlias.objects.filter(email="billing+stripe@test.com").exists())
        self.assertEqual(
            PaymentAccountMismatch.objects.get(stripe_session_id="cs_alias_1105").reason,
            PaymentAccountMismatch.REASON_LEGACY_REFERENCE_MISMATCH,
        )

    def test_client_reference_primary_email_collision_records_mismatch(self):
        paid_user = User.objects.create_user(email="member-collision@test.com")
        candidate = User.objects.create_user(email="billing-collision@test.com")

        session_data = {
            "id": "cs_primary_collision_1105",
            "customer": "cus_primary_collision",
            "customer_details": {"email": candidate.email},
            "subscription": "sub_primary_collision",
            "client_reference_id": str(paid_user.pk),
            "metadata": {"tier_slug": "main"},
        }

        handle_checkout_completed(session_data)
        handle_checkout_completed(session_data)

        paid_user.refresh_from_db()
        candidate.refresh_from_db()
        self.assertEqual(paid_user.tier.slug, "free")
        self.assertEqual(paid_user.subscription_id, "")
        self.assertEqual(candidate.tier.slug, "free")
        self.assertFalse(EmailAlias.objects.filter(email=candidate.email).exists())
        mismatch = PaymentAccountMismatch.objects.get(
            stripe_session_id="cs_primary_collision_1105"
        )
        self.assertEqual(mismatch.status, PaymentAccountMismatch.STATUS_OPEN)
        self.assertEqual(mismatch.reason, PaymentAccountMismatch.REASON_LEGACY_REFERENCE_MISMATCH)
        self.assertEqual(mismatch.paid_user, paid_user)
        self.assertEqual(mismatch.candidate_user, candidate)
        self.assertEqual(mismatch.stripe_email, candidate.email)
        self.assertEqual(
            PaymentAccountMismatch.objects.filter(
                stripe_session_id="cs_primary_collision_1105"
            ).count(),
            1,
        )
        self.assertEqual(
            CommunityAuditLog.objects.filter(
                user=paid_user,
                action="payment_mismatch_recorded",
            ).count(),
            1,
        )

    def test_client_reference_alias_collision_records_mismatch(self):
        paid_user = User.objects.create_user(email="member-alias@test.com")
        candidate = User.objects.create_user(email="candidate-alias@test.com")
        EmailAlias.objects.create(
            user=candidate,
            email="relay-alias@test.com",
            source=EmailAlias.SOURCE_MANUAL,
        )

        session_data = {
            "id": "cs_alias_collision_1105",
            "customer": "cus_alias_collision",
            "customer_details": {"email": "relay-alias@test.com"},
            "subscription": "sub_alias_collision",
            "client_reference_id": str(paid_user.pk),
            "metadata": {"tier_slug": "basic"},
        }

        handle_checkout_completed(session_data)

        paid_user.refresh_from_db()
        candidate.refresh_from_db()
        self.assertEqual(paid_user.tier.slug, "free")
        self.assertEqual(candidate.tier.slug, "free")
        alias = EmailAlias.objects.get(email="relay-alias@test.com")
        self.assertEqual(alias.user, candidate)
        mismatch = PaymentAccountMismatch.objects.get(
            stripe_session_id="cs_alias_collision_1105"
        )
        self.assertEqual(mismatch.reason, PaymentAccountMismatch.REASON_LEGACY_REFERENCE_MISMATCH)
        self.assertEqual(mismatch.paid_user, paid_user)
        self.assertEqual(mismatch.candidate_user, candidate)

    def test_invalid_client_reference_is_quarantined_without_email_fallback(self):
        user = User.objects.create_user(email="fallback-invalid-ref@test.com")

        session_data = {
            "id": "cs_invalid_ref_1105",
            "customer": "cus_invalid_ref",
            "customer_details": {"email": user.email},
            "subscription": "sub_invalid_ref",
            "client_reference_id": "999999",
            "metadata": {"tier_slug": "basic"},
        }

        handle_checkout_completed(session_data)

        user.refresh_from_db()
        self.assertEqual(user.tier.slug, "free")
        self.assertEqual(user.stripe_customer_id, "")
        self.assertEqual(
            PaymentAccountMismatch.objects.get(
                stripe_session_id="cs_invalid_ref_1105"
            ).reason,
            PaymentAccountMismatch.REASON_LEGACY_REFERENCE_MISMATCH,
        )

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
class TierWelcomeEmailRoutingTest(QuietSubscriptionLookupMixin, TestCase):
    """Issue #847: each paid tier routes to its own welcome EmailLog.

    Drives the real ``handle_checkout_completed`` -> ``notify_paid_signup``
    -> ``EmailService`` path (SES short-circuited in test settings) and
    asserts the EmailLog row written carries the tier-specific slug, with
    zero rows for the other two tiers.
    """

    WELCOME_SLUGS = ("basic_welcome", "cofounder_welcome", "premium_welcome")

    def _tier_session(self, user, *, tier_slug):
        return {
            "id": f"cs_847_{tier_slug}",
            "customer": f"cus_847_{tier_slug}",
            "customer_details": {"email": user.email},
            "subscription": f"sub_847_{tier_slug}",
            "client_reference_id": str(user.pk),
            "metadata": {"tier_slug": tier_slug, "user_id": str(user.pk)},
        }

    def _run_and_assert(self, *, tier_slug, expected_slug, email):
        from email_app.models import EmailLog

        user = User.objects.create_user(email=email, first_name="Jess")
        with patch(
            "community.services.staff_notifications.get_config",
            return_value="",
        ):
            handle_checkout_completed(
                self._tier_session(user, tier_slug=tier_slug),
            )

        # Exactly one welcome of the expected slug for this user.
        self.assertEqual(
            EmailLog.objects.filter(
                user=user, email_type=expected_slug,
            ).count(),
            1,
        )
        # Zero welcomes of the other two tier slugs.
        for other in self.WELCOME_SLUGS:
            if other == expected_slug:
                continue
            self.assertEqual(
                EmailLog.objects.filter(
                    user=user, email_type=other,
                ).count(),
                0,
                f"unexpected {other} EmailLog for a {tier_slug} checkout",
            )

    def test_basic_checkout_logs_basic_welcome_only(self):
        self._run_and_assert(
            tier_slug="basic",
            expected_slug="basic_welcome",
            email="basic847@test.com",
        )

    def test_main_checkout_logs_cofounder_welcome_only(self):
        self._run_and_assert(
            tier_slug="main",
            expected_slug="cofounder_welcome",
            email="main847@test.com",
        )

    def test_premium_checkout_logs_premium_welcome_only(self):
        self._run_and_assert(
            tier_slug="premium",
            expected_slug="premium_welcome",
            email="premium847@test.com",
        )

    def test_unsubscribed_basic_buyer_still_gets_welcome(self):
        """The welcome is transactional — an unsubscribed paid user still
        receives their tier welcome.
        """
        from email_app.models import EmailLog

        user = User.objects.create_user(
            email="unsub847@test.com", first_name="Lee", unsubscribed=True,
        )
        with patch(
            "community.services.staff_notifications.get_config",
            return_value="",
        ):
            handle_checkout_completed(
                self._tier_session(user, tier_slug="basic"),
            )

        self.assertEqual(
            EmailLog.objects.filter(
                user=user, email_type="basic_welcome",
            ).count(),
            1,
        )

    def test_free_checkout_sends_no_tier_welcome(self):
        """A level-0 (free) tier checkout fires none of the paid welcomes."""
        from email_app.models import EmailLog

        user = User.objects.create_user(email="free847@test.com")
        handle_checkout_completed(self._tier_session(user, tier_slug="free"))

        for slug in self.WELCOME_SLUGS:
            self.assertEqual(
                EmailLog.objects.filter(
                    user=user, email_type=slug,
                ).count(),
                0,
            )


@tag('core')
class CheckoutAutoVerifyEmailTest(QuietSubscriptionLookupMixin, TestCase):
    """Issue #839: a paid Stripe checkout auto-verifies the entitled email.

    A successful tier checkout flips ``email_verified=True`` on the row
    the entitlement is attached to, before the welcome email sends, so
    payers skip the verify-email footer and reminders. Course purchases
    and unrelated billing-relay accounts are NOT verified.
    """

    def _tier_session(self, user, *, tier_slug, email=None):
        """Build a tier-checkout session for ``user`` (issue #839)."""
        return {
            "id": f"cs_verify_{tier_slug}",
            "customer": f"cus_verify_{tier_slug}",
            "customer_details": {"email": email or user.email},
            "subscription": f"sub_verify_{tier_slug}",
            "client_reference_id": str(user.pk),
            "metadata": {"tier_slug": tier_slug, "user_id": str(user.pk)},
        }

    def test_unverified_payer_is_auto_verified(self):
        """A paid tier checkout flips email_verified on the unverified payer."""
        user = User.objects.create_user(
            email="payer@test.com", email_verified=False,
        )
        basic_tier = Tier.objects.get(slug="basic")

        handle_checkout_completed(self._tier_session(user, tier_slug="basic"))

        user.refresh_from_db()
        self.assertTrue(user.email_verified)
        # account_activated is already provided by the existing
        # mark_activated call (issue #768) — assert, don't duplicate.
        self.assertTrue(user.account_activated)
        self.assertEqual(user.tier, basic_tier)

    def test_already_verified_payer_is_a_noop(self):
        """An already-verified payer stays verified with no redundant save."""
        from accounts.utils.activation import mark_email_verified

        user = User.objects.create_user(
            email="verified@test.com", email_verified=True,
        )

        handle_checkout_completed(self._tier_session(user, tier_slug="basic"))

        user.refresh_from_db()
        self.assertTrue(user.email_verified)
        # The helper's no-op contract: a second call issues no save and
        # returns its no-op signal (False).
        self.assertFalse(mark_email_verified(user))

    def test_welcome_email_omits_verify_footer_for_payer(self):
        """The cofounder_welcome to a paid user carries no verify footer."""
        from email_app.services import EmailService

        user = User.objects.create_user(
            email="mainpayer@test.com", email_verified=False,
        )

        # Main tier (level >= 10) triggers notify_paid_signup ->
        # cofounder_welcome. Run the real handler so email_verified is
        # flipped before any send happens.
        with patch(
            "email_app.services.email_service.EmailService._send_ses",
            return_value="ses-msg-id",
        ) as mock_send_ses:
            handle_checkout_completed(
                self._tier_session(user, tier_slug="main"),
            )

        user.refresh_from_db()
        self.assertTrue(user.email_verified)

        # The footer gate reads email_verified live; verified -> no footer.
        self.assertFalse(
            EmailService()._should_include_verify_footer(
                user, "cofounder_welcome",
            )
        )

        # The cofounder_welcome HTML actually sent via SES carries no
        # verify-email CTA / link.
        welcome_calls = [
            call.args for call in mock_send_ses.call_args_list
        ]
        self.assertTrue(welcome_calls, "no email was sent via SES")
        # The cofounder_welcome is sent to the payer's own address.
        welcome_html = next(
            (args[2] or "")
            for args in welcome_calls
            if args[0] == "mainpayer@test.com"
        )
        # Sanity: this is the welcome body, not an empty/other email.
        self.assertIn("Welcome to the community", welcome_html)
        # The verify-email CTA / link must be absent.
        self.assertNotIn("/api/verify-email", welcome_html)
        self.assertNotIn("verify your email", welcome_html.lower())

    def test_billing_relay_does_not_cross_verify_real_account(self):
        """Only the entitled row flips; an unrelated real account stays unverified.

        Mirrors the prod relay case: the entitlement lands on the relay
        customer account while the person's real account is separate.
        The handler must NOT look up the real account by billing email.

        The guard is exercised genuinely: the Stripe billing email in the
        session payload is set to the REAL account's address (the worst
        case), while ``client_reference_id`` resolves to the relay row.
        If anyone added a "verify the account matching the Stripe billing
        email" lookup, it would flip ``stefanonoventa@gmail.com`` and this
        test would FAIL. Because the handler verifies only the resolved
        ``user`` row, the real account stays unverified.
        """
        # The person's real account — stays unverified.
        real_account = User.objects.create_user(
            email="stefanonoventa@gmail.com", email_verified=False,
        )
        # The relay / customer account the checkout actually resolves to.
        relay_account = User.objects.create_user(
            email="47-gentle.virtual@icloud.com", email_verified=False,
        )

        # client_reference_id resolves to the relay account, but the Stripe
        # billing email points at the REAL account. A billing-email lookup
        # would (wrongly) verify the real account — this locks that out.
        session_data = {
            "id": "cs_relay",
            "customer": "cus_relay",
            "customer_details": {"email": real_account.email},
            "subscription": "sub_relay",
            "client_reference_id": str(relay_account.pk),
            "metadata": {"tier_slug": "basic", "user_id": str(relay_account.pk)},
        }

        handle_checkout_completed(session_data)

        relay_account.refresh_from_db()
        real_account.refresh_from_db()
        # Conflicting legacy evidence is quarantined; neither row is verified.
        self.assertFalse(relay_account.email_verified)
        # The real account matching the billing email is NOT cross-verified.
        self.assertFalse(real_account.email_verified)

    def test_verify_happens_before_notify_paid_signup(self):
        """email_verified is flipped before notify_paid_signup runs."""
        user = User.objects.create_user(
            email="ordering@test.com", email_verified=False,
        )

        observed = {}

        def _capture(*args, **kwargs):
            # Read the verification state at the moment the notification
            # fires — the helper must have already flipped it.
            passed_user = kwargs.get("user")
            passed_user.refresh_from_db()
            observed["verified_at_notify"] = passed_user.email_verified

        with patch(
            "community.services.staff_notifications.notify_paid_signup",
            side_effect=_capture,
        ):
            handle_checkout_completed(
                self._tier_session(user, tier_slug="main"),
            )

        self.assertTrue(
            observed.get("verified_at_notify"),
            "email_verified must be True before notify_paid_signup fires",
        )

    def test_course_purchase_does_not_auto_verify(self):
        """A course purchase grants access but does NOT flip email_verified."""
        from content.models import Course

        user = User.objects.create_user(
            email="coursebuyer@test.com", email_verified=False,
        )
        course = Course.objects.create(
            slug="auto-verify-course",
            title="Auto Verify Course",
        )

        session_data = {
            "id": "cs_course",
            "customer": "cus_course",
            "customer_details": {"email": user.email},
            "client_reference_id": str(user.pk),
            "metadata": {"course_id": str(course.pk), "user_id": str(user.pk)},
        }

        handle_checkout_completed(session_data)

        user.refresh_from_db()
        self.assertFalse(user.email_verified)


def _resolver_subscription(
    subscription_id="sub_resolver",
    *,
    price_id="price_unknown_xyz",
    current_period_end=1_800_000_000,
    price_metadata=None,
    unit_amount=None,
    interval=None,
):
    """Build the Stripe-subscription payload returned by ``retrieve``.

    Mirrors the helper in ``test_backfill_stripe_tiers.py`` /
    ``api/tests/test_tier_reconcile.py`` so the 3-step resolver inputs are
    identical in shape across the webhook, backfill, and reconcile tests.
    """
    price_obj = {
        "id": price_id,
        "metadata": dict(price_metadata or {}),
    }
    if unit_amount is not None:
        price_obj["unit_amount"] = unit_amount
    if interval is not None:
        price_obj["recurring"] = {"interval": interval}
    return {
        "id": subscription_id,
        "status": "active",
        "current_period_end": current_period_end,
        "items": {"data": [{"price": price_obj}]},
    }


def _stripe_client_returning(subscription_payload):
    """Build a ``MagicMock`` Stripe client whose ``subscriptions.retrieve``
    returns the given payload.

    Returned from ``side_effect`` on the ``_get_stripe_client`` patch so
    every call to the helper hands back a fresh client bound to the same
    payload.
    """
    def factory(*args, **kwargs):
        client = MagicMock()
        client.subscriptions.retrieve.return_value = subscription_payload
        return client
    return factory


@tag('core')
@override_settings(STRIPE_SECRET_KEY="sk_test_resolver")
class CheckoutCompletedResolverFallbackTest(TestCase):
    """3-step resolver fallback inside ``handle_checkout_completed`` (#663).

    The session has no ``tier_slug`` metadata (Payment-Link case). The
    handler must fall back to:

      1. ``price.metadata.tier_slug``
      2. ``Tier.stripe_price_id_*`` map
      3. ``price.unit_amount`` + ``recurring.interval``

    and write tier/customer/subscription/period-end whenever any step
    resolves.
    """

    @classmethod
    def setUpTestData(cls):
        cls.main = Tier.objects.get(slug="main")
        cls.basic = Tier.objects.get(slug="basic")
        # Since #684, the bootstrap migration only seeds slug/level/name;
        # yaml content sync writes Stripe IDs and EUR prices. The
        # amount-based resolver fallback in step 3 of these tests needs
        # main's EUR price columns, so configure them inline.
        cls.main.stripe_price_id_yearly = "price_main_yearly"
        cls.main.price_eur_month = 50
        cls.main.price_eur_year = 500
        cls.main.save(update_fields=[
            "stripe_price_id_yearly",
            "price_eur_month",
            "price_eur_year",
        ])

    def setUp(self):
        super().setUp()
        # The post-resolution path calls ``_get_subscription_period_end``
        # and ``_get_subscription_price_id``; both reach real Stripe in
        # the absence of a stub. We replace them with deterministic
        # values driven by the mocked subscription payload so the tests
        # never touch the network.
        self._period_end_patcher = patch(
            "payments.services._get_subscription_period_end",
            return_value=None,
        )
        self._period_end_mock = self._period_end_patcher.start()
        self.addCleanup(self._period_end_patcher.stop)
        self._price_id_patcher = patch(
            "payments.services._get_subscription_price_id",
            return_value="",
        )
        self._price_id_patcher.start()
        self.addCleanup(self._price_id_patcher.stop)

    def _expect_period_end(self, period_end):
        """Configure the patched ``_get_subscription_period_end`` to
        return the timestamp the test wants written on the user. Mirrors
        what Stripe would return for the same payload, but without the
        network round-trip.
        """
        self._period_end_mock.return_value = _dt.fromtimestamp(
            period_end, tz=_tz.utc,
        )

    def _session_data(self, *, subscription_id="sub_resolver", metadata=None):
        return {
            "id": "cs_payment_link_resolver",
            "customer": "cus_resolver",
            "customer_details": {"email": "payer@test.com"},
            "subscription": subscription_id,
            "client_reference_id": None,
            "metadata": metadata if metadata is not None else {},
        }

    def test_checkout_resolves_tier_from_price_metadata_when_session_metadata_empty(
        self,
    ):
        """Step 1 of the resolver: ``price.metadata.tier_slug``."""
        user = User.objects.create_user(email="payer@test.com")
        period_end = 1_810_000_000
        sub_payload = _resolver_subscription(
            subscription_id="sub_meta",
            price_id="price_truly_unknown",
            current_period_end=period_end,
            price_metadata={"tier_slug": "main"},
        )
        self._expect_period_end(period_end)

        with patch(
            "payments.services._get_stripe_client",
            side_effect=_stripe_client_returning(sub_payload),
        ):
            handle_checkout_completed(
                self._session_data(subscription_id="sub_meta"),
            )

        user.refresh_from_db()
        self.assertEqual(user.tier, self.main)
        self.assertEqual(user.stripe_customer_id, "cus_resolver")
        self.assertEqual(user.subscription_id, "sub_meta")
        self.assertEqual(
            user.billing_period_end,
            _dt.fromtimestamp(period_end, tz=_tz.utc),
        )

    def test_checkout_resolves_tier_from_price_id_map_when_session_metadata_empty(self):
        """Step 2 of the resolver: ``Tier.stripe_price_id_*`` map."""
        user = User.objects.create_user(email="payer@test.com")
        period_end = 1_815_000_000
        sub_payload = _resolver_subscription(
            subscription_id="sub_dbmap",
            price_id="price_main_yearly",  # in the DB map via setUpTestData
            current_period_end=period_end,
        )
        self._expect_period_end(period_end)

        with patch(
            "payments.services._get_stripe_client",
            side_effect=_stripe_client_returning(sub_payload),
        ):
            handle_checkout_completed(
                self._session_data(subscription_id="sub_dbmap"),
            )

        user.refresh_from_db()
        self.assertEqual(user.tier, self.main)
        self.assertEqual(user.subscription_id, "sub_dbmap")
        self.assertEqual(user.stripe_customer_id, "cus_resolver")
        self.assertEqual(
            user.billing_period_end,
            _dt.fromtimestamp(period_end, tz=_tz.utc),
        )

    def test_checkout_resolves_tier_from_amount_and_interval_when_price_id_unknown(self):
        """Step 3 (the casraysa case): unit_amount + interval match.

        Stripe returns ``unit_amount=50000`` (500 EUR cents) and
        ``interval="year"``; ``main`` has ``price_eur_year=500`` per the
        seed migration, so the resolver picks ``main``.
        """
        user = User.objects.create_user(email="payer@test.com")
        period_end = 1_820_000_000
        sub_payload = _resolver_subscription(
            subscription_id="sub_amount",
            price_id="price_dashboard_only",
            current_period_end=period_end,
            unit_amount=50000,
            interval="year",
        )
        self._expect_period_end(period_end)

        with patch(
            "payments.services._get_stripe_client",
            side_effect=_stripe_client_returning(sub_payload),
        ):
            handle_checkout_completed(
                self._session_data(subscription_id="sub_amount"),
            )

        user.refresh_from_db()
        self.assertEqual(user.tier, self.main)
        self.assertEqual(user.subscription_id, "sub_amount")
        self.assertEqual(user.stripe_customer_id, "cus_resolver")
        self.assertEqual(
            user.billing_period_end,
            _dt.fromtimestamp(period_end, tz=_tz.utc),
        )

    def test_checkout_logs_session_subscription_and_price_when_all_resolver_steps_fail(
        self,
    ):
        """When every resolver step misses, the error log carries the
        full triage triplet: ``session_id``, ``subscription_id``, and
        ``price_id``.
        """
        user = User.objects.create_user(email="payer@test.com")
        sub_payload = _resolver_subscription(
            subscription_id="sub_truly_unknown",
            price_id="price_truly_unknown",
            unit_amount=99999,
            interval="month",
        )

        with patch(
            "payments.services._get_stripe_client",
            side_effect=_stripe_client_returning(sub_payload),
        ):
            with self.assertLogs("payments.services", level="ERROR") as logs:
                handle_checkout_completed(self._session_data(
                    subscription_id="sub_truly_unknown",
                ))

        user.refresh_from_db()
        self.assertEqual(user.tier.slug, "free")
        joined = "\n".join(logs.output)
        self.assertIn("Could not determine tier", joined)
        self.assertIn("cs_payment_link_resolver", joined)
        self.assertIn("sub_truly_unknown", joined)
        self.assertIn("price_truly_unknown", joined)

    def test_session_metadata_tier_slug_keeps_precedence_over_resolver(self):
        """Session-metadata ``tier_slug`` wins even when the Stripe
        subscription's price would resolve to a different tier.

        Documents that the 3-step resolver is a fallback for empty
        session metadata, never an override of an explicit operator
        signal. ``basic`` here was set by the user/operator on the
        Checkout Session metadata, so it must win over ``main`` from
        the Stripe price.
        """
        user = User.objects.create_user(email="payer@test.com")

        # Patch ``_retrieve_subscription_with_price`` directly so we can
        # assert the resolver fallback was NOT taken — when session
        # metadata already provides a valid tier the handler must never
        # call into the resolver path.
        with patch(
            "payments.services.webhook_handlers._retrieve_subscription_with_price",
        ) as retrieve_mock:
            handle_checkout_completed(
                self._session_data(
                    subscription_id="sub_metadata_wins",
                    metadata={"tier_slug": "basic"},
                ),
            )

        user.refresh_from_db()
        self.assertEqual(user.tier, self.basic)
        # Resolver helper was never invoked because session metadata
        # already supplied a valid tier (resolver is a fallback, not an
        # override).
        self.assertEqual(retrieve_mock.call_count, 0)


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

    def test_cancel_at_period_end_sets_pending_tier_free_keeps_paid(self):
        """Issue #968: cancel_at_period_end=True schedules the cancellation
        by setting pending_tier=free while leaving tier and subscription_id
        unchanged (the user keeps paid access until subscription.deleted)."""
        basic_tier = Tier.objects.get(slug="basic")
        free_tier = Tier.objects.get(slug="free")
        user = User.objects.create_user(email="cancel_pending@test.com")
        user.tier = basic_tier
        user.subscription_id = "sub_cancel_pending"
        user.save(update_fields=["tier", "subscription_id"])

        subscription_data = {
            "id": "sub_cancel_pending",
            "customer": "cus_cancel_pending",
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
        self.assertEqual(user.pending_tier, free_tier)
        # tier and subscription_id are untouched -- still fully paid.
        self.assertEqual(user.tier, basic_tier)
        self.assertEqual(user.subscription_id, "sub_cancel_pending")
        self.assertIsNotNone(user.billing_period_end)

    def test_reactivation_clears_pending_tier(self):
        """Issue #968: a follow-up cancel_at_period_end=False update (user
        un-cancelled in the portal) clears pending_tier back to None."""
        basic_tier = Tier.objects.get(slug="basic")
        free_tier = Tier.objects.get(slug="free")
        basic_tier.stripe_price_id_monthly = "price_basic_monthly"
        basic_tier.save()

        user = User.objects.create_user(email="reactivate@test.com")
        user.tier = basic_tier
        user.subscription_id = "sub_reactivate"
        # Simulate a prior scheduled cancellation.
        user.pending_tier = free_tier
        user.save(update_fields=["tier", "subscription_id", "pending_tier"])

        subscription_data = {
            "id": "sub_reactivate",
            "customer": "cus_reactivate",
            "status": "active",
            "cancel_at_period_end": False,
            "current_period_end": 1774396800,
            "items": {
                "data": [
                    {"price": {"id": "price_basic_monthly"}},
                ],
            },
        }

        handle_subscription_updated(subscription_data)

        user.refresh_from_db()
        self.assertIsNone(user.pending_tier)
        # Re-activation keeps the user on their paid tier.
        self.assertEqual(user.tier, basic_tier)
        self.assertEqual(user.subscription_id, "sub_reactivate")

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

    def test_customer_fallback_does_not_delete_newer_subscription(self):
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
        self.assertEqual(user.tier.slug, "main")
        self.assertEqual(user.subscription_id, "sub_old")
        self.assertTrue(
            CommunityAuditLog.objects.filter(
                user=user, action="stale_subscription_event_ignored"
            ).exists()
        )

    def test_no_error_when_user_not_found(self):
        """Handler does not crash when no user matches the subscription."""
        subscription_data = {
            "id": "sub_ghost",
            "customer": "cus_ghost",
        }

        # Should not raise
        handle_subscription_deleted(subscription_data)


@tag('core')
class StripeStatusTagReconciliationTest(QuietSubscriptionLookupMixin, TestCase):
    """Stripe status-tag reconciliation across webhook handlers (issue #969).

    Each test fires a webhook handler with a known starting tag set and asserts
    the post-handler ``user.tags`` matches the transition table: ``stripe:active``
    / ``stripe:churned`` flip correctly, exactly one ``stripe:plan-*`` survives,
    ``stripe:imported`` is preserved, and non-``stripe:`` tags are untouched.
    """

    @classmethod
    def setUpTestData(cls):
        cls.free = Tier.objects.get(slug="free")
        cls.basic = Tier.objects.get(slug="basic")
        cls.main = Tier.objects.get(slug="main")
        # Map a price id onto each tier so handle_subscription_updated can
        # resolve a tier change via ``_tier_for_price_id``.
        cls.basic.stripe_price_id_monthly = "price_basic_monthly"
        cls.basic.save(update_fields=["stripe_price_id_monthly"])
        cls.main.stripe_price_id_monthly = "price_main_monthly"
        cls.main.save(update_fields=["stripe_price_id_monthly"])

    @staticmethod
    def _plan_tags(user):
        return [t for t in user.tags if t.startswith("stripe:plan-")]

    def _make_user(self, *, tier, tags, subscription_id="", customer_id=""):
        user = User.objects.create_user(email=f"tags-{User.objects.count()}@test.com")
        user.tier = tier
        user.tags = list(tags)
        user.subscription_id = subscription_id
        user.stripe_customer_id = customer_id
        user.save(update_fields=[
            "tier", "tags", "subscription_id", "stripe_customer_id",
        ])
        return user

    def test_resubscribe_sheds_churned_tag(self):
        """The Kir case: a churned user who completes checkout goes active."""
        user = self._make_user(
            tier=self.free,
            tags=["stripe:imported", "stripe:churned"],
            customer_id="cus_kir",
        )

        handle_checkout_completed({
            "id": "cs_kir",
            "customer": "cus_kir",
            "subscription": "sub_kir",
            "client_reference_id": str(user.pk),
            "customer_details": {"email": user.email},
            "metadata": {"tier_slug": "main"},
        })

        user.refresh_from_db()
        self.assertIn("stripe:active", user.tags)
        self.assertNotIn("stripe:churned", user.tags)
        self.assertEqual(self._plan_tags(user), ["stripe:plan-main"])
        self.assertIn("stripe:imported", user.tags)
        self.assertEqual(user.tier, self.main)

    def test_deletion_churns_active_member(self):
        """customer.subscription.deleted removes active/plan, adds churned."""
        user = self._make_user(
            tier=self.main,
            tags=["stripe:imported", "stripe:active", "stripe:plan-main"],
            subscription_id="sub_del",
            customer_id="cus_del",
        )

        handle_subscription_deleted({"id": "sub_del", "customer": "cus_del"})

        user.refresh_from_db()
        self.assertIn("stripe:churned", user.tags)
        self.assertNotIn("stripe:active", user.tags)
        self.assertEqual(self._plan_tags(user), [])
        self.assertEqual(user.tier, self.free)

    def test_upgrade_swaps_plan_tag(self):
        """An active plan change Basic -> Main swaps the plan tag."""
        user = self._make_user(
            tier=self.basic,
            tags=["stripe:active", "stripe:plan-basic"],
            subscription_id="sub_up",
            customer_id="cus_up",
        )

        handle_subscription_updated({
            "id": "sub_up",
            "customer": "cus_up",
            "status": "active",
            "cancel_at_period_end": False,
            "current_period_end": 1700000000,
            "items": {"data": [{"price": {"id": "price_main_monthly"}}]},
        })

        user.refresh_from_db()
        self.assertEqual(self._plan_tags(user), ["stripe:plan-main"])
        self.assertIn("stripe:active", user.tags)
        self.assertNotIn("stripe:churned", user.tags)

    def test_cancel_at_period_end_does_not_churn_tag(self):
        """cancel_at_period_end keeps active + current plan tag."""
        user = self._make_user(
            tier=self.main,
            tags=["stripe:active", "stripe:plan-main"],
            subscription_id="sub_cape",
            customer_id="cus_cape",
        )

        handle_subscription_updated({
            "id": "sub_cape",
            "customer": "cus_cape",
            "status": "active",
            "cancel_at_period_end": True,
            "current_period_end": 1700000000,
            "items": {"data": [{"price": {"id": "price_main_monthly"}}]},
        })

        user.refresh_from_db()
        self.assertIn("stripe:active", user.tags)
        self.assertIn("stripe:plan-main", user.tags)
        self.assertNotIn("stripe:churned", user.tags)

    def test_payment_failed_leaves_status_tags_untouched(self):
        """invoice.payment_failed must not mutate any stripe:* tag."""
        user = self._make_user(
            tier=self.main,
            tags=["stripe:active", "stripe:plan-main"],
            customer_id="cus_pf",
        )
        before = list(user.tags)

        with patch("payments.services.send_mail"):
            handle_invoice_payment_failed({
                "customer": "cus_pf",
                "customer_email": user.email,
            })

        user.refresh_from_db()
        self.assertEqual(user.tags, before)
        self.assertEqual(user.tier, self.main)

    def test_reconciliation_never_touches_unrelated_tags(self):
        """A non-stripe tag like slack-member is preserved on churn."""
        user = self._make_user(
            tier=self.main,
            tags=["slack-member", "stripe:imported", "stripe:active", "stripe:plan-main"],
            subscription_id="sub_unrel",
            customer_id="cus_unrel",
        )

        handle_subscription_deleted({"id": "sub_unrel", "customer": "cus_unrel"})

        user.refresh_from_db()
        self.assertIn("slack-member", user.tags)
        self.assertIn("stripe:imported", user.tags)
        self.assertIn("stripe:churned", user.tags)
        self.assertNotIn("stripe:active", user.tags)
        self.assertEqual(self._plan_tags(user), [])

    def test_replaying_webhook_is_noop_on_tags(self):
        """Firing the same active update twice yields an identical tag set."""
        user = self._make_user(
            tier=self.main,
            tags=["stripe:active", "stripe:plan-main"],
            subscription_id="sub_replay",
            customer_id="cus_replay",
        )
        event = {
            "id": "sub_replay",
            "customer": "cus_replay",
            "status": "active",
            "cancel_at_period_end": False,
            "current_period_end": 1700000000,
            "items": {"data": [{"price": {"id": "price_main_monthly"}}]},
        }

        handle_subscription_updated(event)
        user.refresh_from_db()
        after_first = list(user.tags)

        handle_subscription_updated(event)
        user.refresh_from_db()

        self.assertEqual(user.tags, after_first)
        self.assertEqual(user.tags.count("stripe:active"), 1)
        self.assertEqual(self._plan_tags(user), ["stripe:plan-main"])

    def test_imported_absent_user_does_not_gain_it(self):
        """A user without stripe:imported never gains it on reconciliation."""
        user = self._make_user(
            tier=self.main,
            tags=["stripe:active", "stripe:plan-main"],
            subscription_id="sub_noimp",
            customer_id="cus_noimp",
        )

        handle_subscription_deleted({"id": "sub_noimp", "customer": "cus_noimp"})

        user.refresh_from_db()
        self.assertNotIn("stripe:imported", user.tags)


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

    @patch("payments.services.send_mail")
    def test_email_transport_error_is_logged_without_revoking_tier(
        self, mock_send_mail,
    ):
        """Mail transport failures are visible but do not change membership."""
        mock_send_mail.side_effect = SMTPException("smtp down")
        main_tier = Tier.objects.get(slug="main")
        user = User.objects.create_user(email="smtpdown@test.com")
        user.tier = main_tier
        user.stripe_customer_id = "cus_smtpdown"
        user.save(update_fields=["tier", "stripe_customer_id"])
        invoice_data = {
            "customer": "cus_smtpdown",
            "customer_email": "smtpdown@test.com",
        }

        with patch("payments.services.logger") as mock_logger:
            handle_invoice_payment_failed(invoice_data)

        user.refresh_from_db()
        self.assertEqual(user.tier, main_tier)
        mock_logger.exception.assert_called_once()

    @patch("payments.services.send_mail")
    def test_unexpected_email_error_propagates_for_webhook_retry(
        self, mock_send_mail,
    ):
        """Programmer errors should not be converted into processed webhooks."""
        mock_send_mail.side_effect = RuntimeError("template bug")
        user = User.objects.create_user(
            email="emailbug@test.com",
            stripe_customer_id="cus_emailbug",
        )
        invoice_data = {
            "customer": user.stripe_customer_id,
            "customer_email": user.email,
        }

        with self.assertRaisesMessage(RuntimeError, "template bug"):
            handle_invoice_payment_failed(invoice_data)


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
