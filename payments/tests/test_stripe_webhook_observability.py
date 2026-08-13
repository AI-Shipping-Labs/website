"""Durable delivery-attempt evidence, cancellation contract, and endpoint
verification for Stripe cancellation webhooks (issue #1314)."""

import hashlib
import hmac
import json
import time
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase, override_settings, tag
from django.utils import timezone

from accounts.models import User
from payments.exceptions import (
    WebhookAmbiguousUserError,
    WebhookUnmatchedUserError,
)
from payments.models import (
    StripeWebhookDeliveryAttempt,
    StripeWebhookEndpointCheck,
    Tier,
    WebhookEvent,
)
from payments.services import handle_subscription_updated
from payments.services.stripe_endpoint_verifier import (
    signing_secret_evidence,
    verify_stripe_endpoint,
)
from payments.services.subscription_resolution import resolve_subscription_user

WEBHOOK_URL = "/api/webhooks/payments"
TEST_WEBHOOK_SECRET = "whsec_test_secret_key_for_testing"


def _sig(payload_bytes, secret=TEST_WEBHOOK_SECRET):
    timestamp = str(int(time.time()))
    signed = f"{timestamp}.{payload_bytes.decode('utf-8')}"
    signature = hmac.new(
        secret.encode(), signed.encode(), hashlib.sha256,
    ).hexdigest()
    return f"t={timestamp},v1={signature}"


def _event(event_id, event_type, obj, livemode=False):
    return {
        "id": event_id,
        "type": event_type,
        "livemode": livemode,
        "data": {"object": obj},
    }


def _post(client, event_id, event_type, obj, livemode=False):
    payload = json.dumps(_event(event_id, event_type, obj, livemode)).encode()
    return client.post(
        WEBHOOK_URL,
        data=payload,
        content_type="application/json",
        HTTP_STRIPE_SIGNATURE=_sig(payload),
    )


@tag("core")
@override_settings(STRIPE_WEBHOOK_SECRET=TEST_WEBHOOK_SECRET)
class DeliveryAttemptTest(TestCase):
    """A secret-free attempt is persisted for every handled delivery."""

    def _paid_user(self, email, sub="sub_1", cus="cus_1"):
        user = User.objects.create_user(email=email)
        user.tier = Tier.objects.get(slug="main")
        user.subscription_id = sub
        user.stripe_customer_id = cus
        user.save(update_fields=["tier", "subscription_id", "stripe_customer_id"])
        return user

    def test_processed_delivery_records_attempt_and_terminal_event(self):
        self._paid_user("proc@test.com")
        resp = _post(
            self.client, "evt_p1", "customer.subscription.deleted",
            {"id": "sub_1", "customer": "cus_1"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "processed")

        attempt = StripeWebhookDeliveryAttempt.objects.get(stripe_event_id="evt_p1")
        self.assertEqual(attempt.outcome, "processed")
        self.assertEqual(attempt.http_status, 200)
        self.assertEqual(attempt.attempt_number, 1)
        self.assertEqual(attempt.source, "stripe_delivery")
        self.assertEqual(attempt.stripe_subscription_id, "sub_1")
        self.assertEqual(attempt.stripe_customer_id, "cus_1")
        self.assertIsNotNone(attempt.finished_at)
        self.assertTrue(
            WebhookEvent.objects.filter(
                stripe_event_id="evt_p1", status=WebhookEvent.STATUS_PROCESSED,
            ).exists()
        )

    def test_unmatched_user_is_retryable_500_without_terminal_event(self):
        resp = _post(
            self.client, "evt_u1", "customer.subscription.deleted",
            {"id": "sub_missing", "customer": "cus_missing"},
        )
        self.assertEqual(resp.status_code, 500)
        attempt = StripeWebhookDeliveryAttempt.objects.get(stripe_event_id="evt_u1")
        self.assertEqual(attempt.outcome, "unmatched_user")
        self.assertEqual(attempt.http_status, 500)
        self.assertFalse(
            WebhookEvent.objects.filter(stripe_event_id="evt_u1").exists(),
            "Retryable unmatched_user must NOT create a terminal WebhookEvent.",
        )

    def test_retry_after_unmatched_increments_attempt_number_and_processes(self):
        # First delivery: user not created yet -> unmatched, retryable.
        _post(
            self.client, "evt_r1", "customer.subscription.deleted",
            {"id": "sub_r", "customer": "cus_r"},
        )
        # Local checkout fulfillment now creates the user.
        self._paid_user("retry@test.com", sub="sub_r", cus="cus_r")
        # Stripe retries the SAME event id.
        resp = _post(
            self.client, "evt_r1", "customer.subscription.deleted",
            {"id": "sub_r", "customer": "cus_r"},
        )
        self.assertEqual(resp.status_code, 200)
        attempts = list(
            StripeWebhookDeliveryAttempt.objects
            .filter(stripe_event_id="evt_r1").order_by("attempt_number")
        )
        self.assertEqual([a.attempt_number for a in attempts], [1, 2])
        self.assertEqual(attempts[0].outcome, "unmatched_user")
        self.assertEqual(attempts[1].outcome, "processed")

    def test_ambiguous_user_is_terminal_mutates_nobody_and_alerts_once(self):
        # Two users share the customer id and both have blank subscription.
        u1 = User.objects.create_user(email="amb1@test.com")
        u2 = User.objects.create_user(email="amb2@test.com")
        main = Tier.objects.get(slug="main")
        for u in (u1, u2):
            u.tier = main
            u.stripe_customer_id = "cus_amb"
            u.save(update_fields=["tier", "stripe_customer_id"])

        with patch("payments.services.webhook_dispatch.mail_admins") as mock_mail:
            resp = _post(
                self.client, "evt_a1", "customer.subscription.deleted",
                {"id": "sub_amb", "customer": "cus_amb"},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ambiguous_user")
        attempt = StripeWebhookDeliveryAttempt.objects.get(stripe_event_id="evt_a1")
        self.assertEqual(attempt.outcome, "ambiguous_user")
        # Nobody was mutated.
        u1.refresh_from_db()
        u2.refresh_from_db()
        self.assertEqual(u1.tier, main)
        self.assertEqual(u2.tier, main)
        # Terminal row + exactly one alert.
        self.assertTrue(
            WebhookEvent.objects.filter(
                stripe_event_id="evt_a1",
                status=WebhookEvent.STATUS_FAILED_PERMANENT,
            ).exists()
        )
        self.assertEqual(mock_mail.call_count, 1)

    def test_transient_failure_rolls_back_and_leaves_no_terminal_event(self):
        user = self._paid_user("trans@test.com", sub="sub_t", cus="cus_t")
        main = Tier.objects.get(slug="main")
        # Issue #1308: the deleted path now churns tags through the shared
        # ``subscription_transition.apply_ended_subscription``; inject the
        # transient failure there so the rollback assertion still holds.
        with patch(
            "payments.services.subscription_transition.reconcile_stripe_status_tags",
            side_effect=RuntimeError("boom"),
        ):
            resp = _post(
                self.client, "evt_t1", "customer.subscription.deleted",
                {"id": "sub_t", "customer": "cus_t"},
            )
        self.assertEqual(resp.status_code, 500)
        attempt = StripeWebhookDeliveryAttempt.objects.get(stripe_event_id="evt_t1")
        self.assertEqual(attempt.outcome, "failed_transient")
        user.refresh_from_db()
        self.assertEqual(
            user.tier, main,
            "A transient failure must roll back the local tier change.",
        )
        self.assertEqual(user.subscription_id, "sub_t")
        self.assertFalse(
            WebhookEvent.objects.filter(stripe_event_id="evt_t1").exists()
        )

    def test_already_processed_second_delivery_records_attempt(self):
        self._paid_user("dup@test.com", sub="sub_d", cus="cus_d")
        _post(
            self.client, "evt_d1", "customer.subscription.deleted",
            {"id": "sub_d", "customer": "cus_d"},
        )
        resp = _post(
            self.client, "evt_d1", "customer.subscription.deleted",
            {"id": "sub_d", "customer": "cus_d"},
        )
        self.assertEqual(resp.json()["status"], "already_processed")
        outcomes = set(
            StripeWebhookDeliveryAttempt.objects
            .filter(stripe_event_id="evt_d1")
            .values_list("outcome", flat=True)
        )
        self.assertEqual(outcomes, {"processed", "already_processed"})

    def test_invalid_signature_persists_no_attempt(self):
        payload = json.dumps(
            _event("evt_bad", "customer.subscription.deleted",
                   {"id": "sub_x", "customer": "cus_x"})
        ).encode()
        resp = self.client.post(
            WEBHOOK_URL, data=payload, content_type="application/json",
            HTTP_STRIPE_SIGNATURE="t=1,v1=deadbeef",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(StripeWebhookDeliveryAttempt.objects.exists())

    def test_unknown_event_type_persists_no_attempt(self):
        resp = _post(self.client, "evt_unknown", "charge.succeeded", {"id": "ch_1"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ignored")
        self.assertFalse(StripeWebhookDeliveryAttempt.objects.exists())


@tag("core")
class UniqueResolutionTest(TestCase):
    def test_zero_match_raises_unmatched(self):
        with self.assertRaises(WebhookUnmatchedUserError):
            resolve_subscription_user("sub_none", "cus_none")

    def test_duplicate_customer_raises_ambiguous(self):
        main = Tier.objects.get(slug="main")
        for email in ("d1@test.com", "d2@test.com"):
            u = User.objects.create_user(email=email)
            u.tier = main
            u.stripe_customer_id = "cus_dup"
            u.save(update_fields=["tier", "stripe_customer_id"])
        with self.assertRaises(WebhookAmbiguousUserError):
            resolve_subscription_user("", "cus_dup")

    def test_subscription_id_match_wins(self):
        u = User.objects.create_user(email="win@test.com")
        u.subscription_id = "sub_win"
        u.stripe_customer_id = "cus_win"
        u.save(update_fields=["subscription_id", "stripe_customer_id"])
        res = resolve_subscription_user("sub_win", "cus_win")
        self.assertEqual(res.user, u)
        self.assertEqual(res.matched_by, "subscription_id")


@tag("core")
class CancellationContractTest(TestCase):
    def _user(self, sub="sub_c", cus="cus_c"):
        u = User.objects.create_user(email="cancel@test.com")
        u.tier = Tier.objects.get(slug="main")
        u.subscription_id = sub
        u.stripe_customer_id = cus
        u.save(update_fields=["tier", "subscription_id", "stripe_customer_id"])
        return u

    def test_future_cancel_at_schedules_without_removing_access(self):
        u = self._user()
        main = Tier.objects.get(slug="main")
        free = Tier.objects.get(slug="free")
        future = int(time.time()) + 60 * 60 * 24 * 30
        handle_subscription_updated({
            "id": "sub_c",
            "customer": "cus_c",
            "status": "active",
            "cancel_at_period_end": False,
            "cancel_at": future,
            "items": {"data": [{"price": {"id": "price_x"}}]},
        })
        u.refresh_from_db()
        self.assertEqual(u.tier, main, "Paid access is retained.")
        self.assertEqual(u.pending_tier, free)
        self.assertEqual(u.subscription_id, "sub_c")
        self.assertIsNotNone(u.billing_period_end)
        # billing_period_end is driven by cancel_at.
        self.assertEqual(int(u.billing_period_end.timestamp()), future)

    def test_dunning_state_does_not_clear_pending_cancellation(self):
        u = self._user()
        free = Tier.objects.get(slug="free")
        u.pending_tier = free
        u.save(update_fields=["pending_tier"])
        handle_subscription_updated({
            "id": "sub_c",
            "customer": "cus_c",
            "status": "past_due",
            "cancel_at_period_end": False,
            "items": {"data": [{"price": {"id": "price_x"}}]},
        })
        u.refresh_from_db()
        self.assertEqual(
            u.pending_tier, free,
            "past_due must not clear a scheduled cancellation.",
        )

    def test_explicit_no_cancellation_clears_pending(self):
        u = self._user()
        free = Tier.objects.get(slug="free")
        u.pending_tier = free
        u.save(update_fields=["pending_tier"])
        handle_subscription_updated({
            "id": "sub_c",
            "customer": "cus_c",
            "status": "active",
            "cancel_at_period_end": False,
            "items": {"data": [{"price": {"id": "price_x"}}]},
        })
        u.refresh_from_db()
        self.assertIsNone(u.pending_tier)


def _endpoint(url, events, status="enabled", livemode=True, ep_id="we_1",
              api_version="2024-01-01"):
    return {
        "id": ep_id,
        "url": url,
        "status": status,
        "enabled_events": events,
        "livemode": livemode,
        "api_version": api_version,
    }


ALL_EVENTS = [
    "checkout.session.completed",
    "checkout.session.async_payment_succeeded",
    "checkout.session.async_payment_failed",
    "customer.subscription.updated",
    "customer.subscription.deleted",
    "invoice.payment_failed",
    "invoice.paid",
    "customer.updated",
]
EXPECTED_URL = "https://aishippinglabs.com/api/webhooks/payments"


@tag("core")
class EndpointVerifierTest(TestCase):
    def _run(self, endpoints, secret="sk_live_abc"):
        client = SimpleNamespace(
            webhook_endpoints=SimpleNamespace(
                list=lambda params=None: SimpleNamespace(data=endpoints),
            )
        )

        def fake_config(key, default=""):
            if key == "STRIPE_SECRET_KEY":
                return secret
            if key == "STRIPE_WEBHOOK_EXPECTED_URL":
                return EXPECTED_URL
            return default

        with patch(
            "payments.services.stripe_endpoint_verifier._get_stripe_client",
            return_value=client,
        ), patch(
            "payments.services.stripe_endpoint_verifier.get_config",
            side_effect=fake_config,
        ):
            return verify_stripe_endpoint(
                source=StripeWebhookEndpointCheck.SOURCE_API,
            )

    def test_healthy_endpoint_passes(self):
        check = self._run([_endpoint(EXPECTED_URL, ALL_EVENTS)])
        self.assertEqual(check.status, "pass")
        self.assertEqual(check.key_mode, "live")
        self.assertEqual(check.missing_events, [])
        self.assertEqual(check.matching_endpoint_id, "we_1")
        self.assertTrue(StripeWebhookEndpointCheck.objects.filter(pk=check.pk).exists())

    def test_missing_cancellation_events_fails_and_names_them(self):
        events = ["checkout.session.completed", "invoice.payment_failed", "customer.updated"]
        check = self._run([_endpoint(EXPECTED_URL, events)])
        self.assertEqual(check.status, "fail")
        self.assertEqual(check.error_code, "missing_events")
        self.assertIn("customer.subscription.updated", check.missing_events)
        self.assertIn("customer.subscription.deleted", check.missing_events)
        self.assertIn("customer.subscription.updated", check.error_message)

    def test_endpoint_missing_fails(self):
        check = self._run([_endpoint("https://other.example/hook", ALL_EVENTS)])
        self.assertEqual(check.status, "fail")
        self.assertEqual(check.error_code, "endpoint_missing")

    def test_duplicate_endpoints_warn(self):
        check = self._run([
            _endpoint(EXPECTED_URL, ALL_EVENTS, ep_id="we_1"),
            _endpoint(EXPECTED_URL, ALL_EVENTS, ep_id="we_2"),
        ])
        self.assertEqual(check.status, "warning")
        self.assertEqual(check.error_code, "duplicate_endpoints")

    def test_extra_events_warn(self):
        check = self._run([_endpoint(EXPECTED_URL, ALL_EVENTS + ["charge.refunded"])])
        self.assertEqual(check.status, "warning")
        self.assertEqual(check.error_code, "unexpected_events")
        self.assertIn("charge.refunded", check.unexpected_events)

    def test_permission_error_is_not_endpoint_missing(self):
        import stripe as stripe_mod

        def raising_list(params=None):
            raise stripe_mod.PermissionError("no access")

        client = SimpleNamespace(
            webhook_endpoints=SimpleNamespace(list=raising_list)
        )

        def fake_config(key, default=""):
            if key == "STRIPE_SECRET_KEY":
                return "rk_live_restricted"
            if key == "STRIPE_WEBHOOK_EXPECTED_URL":
                return EXPECTED_URL
            return default

        with patch(
            "payments.services.stripe_endpoint_verifier._get_stripe_client",
            return_value=client,
        ), patch(
            "payments.services.stripe_endpoint_verifier.get_config",
            side_effect=fake_config,
        ):
            check = verify_stripe_endpoint(
                source=StripeWebhookEndpointCheck.SOURCE_API,
            )
        self.assertEqual(check.status, "fail")
        self.assertEqual(check.error_code, "permission_denied")

    def test_missing_secret_key_fails_clearly(self):
        check = self._run([_endpoint(EXPECTED_URL, ALL_EVENTS)], secret="")
        self.assertEqual(check.status, "fail")
        self.assertEqual(check.error_code, "missing_secret_key")


@tag("core")
class SigningSecretEvidenceTest(TestCase):
    @override_settings(STRIPE_WEBHOOK_SECRET="whsec_configured")
    def test_configured_reported(self):
        evidence = signing_secret_evidence()
        self.assertTrue(evidence["configured"])
        self.assertFalse(evidence["verified_by_delivery"])

    def test_verified_by_delivery_after_attempt(self):
        StripeWebhookDeliveryAttempt.objects.create(
            source=StripeWebhookDeliveryAttempt.SOURCE_STRIPE_DELIVERY,
            stripe_event_id="evt_x",
            event_type="customer.subscription.deleted",
            outcome="processed",
            received_at=timezone.now() - timedelta(minutes=5),
        )
        evidence = signing_secret_evidence()
        self.assertTrue(evidence["verified_by_delivery"])
        self.assertIsNotNone(evidence["latest_verified_at"])
