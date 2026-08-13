"""Staff-token API for Stripe webhook observability (issue #1314)."""

from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase, tag

from accounts.models import Token, User
from payments.models import (
    StripeWebhookDeliveryAttempt,
    StripeWebhookEndpointCheck,
    Tier,
    WebhookEvent,
)

VERIFY_URL = "/api/payments/stripe-webhooks/verify"
STATUS_URL = "/api/payments/stripe-webhooks/status"
DELIVERIES_URL = "/api/payments/stripe-webhooks/deliveries"
REPLAY_URL = "/api/payments/stripe-webhooks/replay"

EXPECTED_URL = "https://aishippinglabs.com/api/webhooks/payments"
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


def _endpoint(url=EXPECTED_URL, events=None, status="enabled", livemode=True):
    return {
        "id": "we_1",
        "url": url,
        "status": status,
        "enabled_events": events if events is not None else ALL_EVENTS,
        "livemode": livemode,
        "api_version": "2024-01-01",
    }


def _stripe_event(event_id, event_type, obj, livemode=True):
    return {
        "id": event_id,
        "type": event_type,
        "livemode": livemode,
        "data": {"object": obj},
    }


@tag("core")
class AuthTest(TestCase):
    def test_all_endpoints_require_token(self):
        for method, url in [
            ("post", VERIFY_URL),
            ("get", STATUS_URL),
            ("get", DELIVERIES_URL),
            ("post", REPLAY_URL),
        ]:
            resp = getattr(self.client, method)(
                url, data={}, content_type="application/json",
            )
            self.assertEqual(resp.status_code, 401, f"{method} {url}")

    def test_replay_does_not_call_stripe_without_token(self):
        with patch(
            "payments.services.webhook_replay._get_stripe_client",
        ) as mock_client:
            resp = self.client.post(
                REPLAY_URL,
                data={"event_id": "evt_x"},
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 401)
        mock_client.assert_not_called()


@tag("core")
class StripeWebhookApiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            email="admin@test.com", password="x", is_staff=True,
        )
        cls.token = Token.objects.create(user=cls.admin, name="wh-test")

    def _auth(self):
        return {"HTTP_AUTHORIZATION": f"Token {self.token.key}"}

    def _paid_user(self, email, sub, cus):
        u = User.objects.create_user(email=email)
        u.tier = Tier.objects.get(slug="main")
        u.subscription_id = sub
        u.stripe_customer_id = cus
        u.save(update_fields=["tier", "subscription_id", "stripe_customer_id"])
        return u

    def _mock_verifier(self, endpoints, secret="sk_live_abc"):
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
            if key == "STRIPE_WEBHOOK_SECRET":
                return "whsec_configured"
            return default

        return (
            patch(
                "payments.services.stripe_endpoint_verifier._get_stripe_client",
                return_value=client,
            ),
            patch(
                "payments.services.stripe_endpoint_verifier.get_config",
                side_effect=fake_config,
            ),
        )

    def _mock_replay(self, event_dict, secret="sk_live_abc"):
        client = SimpleNamespace(
            events=SimpleNamespace(retrieve=lambda eid: event_dict),
        )

        def fake_config(key, default=""):
            if key == "STRIPE_SECRET_KEY":
                return secret
            return default

        return (
            patch(
                "payments.services.webhook_replay._get_stripe_client",
                return_value=client,
            ),
            patch(
                "payments.services.webhook_replay.get_config",
                side_effect=fake_config,
            ),
        )

    # ---- verify -------------------------------------------------------
    def test_verify_pass(self):
        p1, p2 = self._mock_verifier([_endpoint()])
        with p1, p2:
            resp = self.client.post(VERIFY_URL, **self._auth())
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "pass")
        self.assertEqual(data["required_events"], ALL_EVENTS)
        self.assertTrue(data["signing_secret"]["configured"])
        self.assertTrue(StripeWebhookEndpointCheck.objects.exists())

    def test_verify_missing_events_fail(self):
        endpoint = _endpoint(events=["checkout.session.completed"])
        p1, p2 = self._mock_verifier([endpoint])
        with p1, p2:
            resp = self.client.post(VERIFY_URL, **self._auth())
        data = resp.json()
        self.assertEqual(data["status"], "fail")
        self.assertIn("customer.subscription.deleted", data["missing_events"])

    # ---- status -------------------------------------------------------
    def test_status_no_stripe_call_with_aggregates(self):
        self._paid_user("m@test.com", "sub_s", "cus_s")
        StripeWebhookDeliveryAttempt.objects.create(
            stripe_event_id="evt_1", event_type="customer.subscription.deleted",
            outcome="processed", http_status=200,
        )
        StripeWebhookDeliveryAttempt.objects.create(
            stripe_event_id="evt_2", event_type="customer.subscription.deleted",
            outcome="unmatched_user", http_status=500,
        )
        resp = self.client.get(STATUS_URL, **self._auth())
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["expected_url"], EXPECTED_URL)
        self.assertEqual(data["cancellation_attempt_counts"]["processed"], 1)
        self.assertEqual(data["cancellation_attempt_counts"]["unmatched_user"], 1)

    # ---- deliveries ---------------------------------------------------
    def test_deliveries_filter_and_paginate(self):
        for i in range(3):
            StripeWebhookDeliveryAttempt.objects.create(
                stripe_event_id=f"evt_{i}",
                event_type="customer.subscription.deleted",
                stripe_customer_id="cus_a" if i == 0 else "cus_b",
                outcome="processed", http_status=200,
            )
        resp = self.client.get(
            DELIVERIES_URL, {"customer_id": "cus_a"}, **self._auth(),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["count"], 1)

        resp2 = self.client.get(
            DELIVERIES_URL, {"page_size": "2"}, **self._auth(),
        )
        self.assertEqual(len(resp2.json()["deliveries"]), 2)
        self.assertEqual(resp2.json()["count"], 3)

    def test_deliveries_invalid_outcome_422(self):
        resp = self.client.get(
            DELIVERIES_URL, {"outcome": "bogus"}, **self._auth(),
        )
        self.assertEqual(resp.status_code, 422)

    def test_deliveries_filters_async_checkout_events(self):
        StripeWebhookDeliveryAttempt.objects.create(
            stripe_event_id="evt_async_success",
            event_type="checkout.session.async_payment_succeeded",
            stripe_object_id="cs_async",
            outcome="processed",
            http_status=200,
        )
        StripeWebhookDeliveryAttempt.objects.create(
            stripe_event_id="evt_other",
            event_type="checkout.session.completed",
            stripe_object_id="cs_other",
            outcome="processed",
            http_status=200,
        )
        response = self.client.get(
            DELIVERIES_URL,
            {"event_type": "checkout.session.async_payment_succeeded"},
            **self._auth(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 1)
        self.assertEqual(
            response.json()["deliveries"][0]["stripe_event_id"],
            "evt_async_success",
        )

    # ---- replay -------------------------------------------------------
    def test_replay_dry_run_previews_without_mutation(self):
        user = self._paid_user("replay@test.com", "sub_rp", "cus_rp")
        event = _stripe_event(
            "evt_rp", "customer.subscription.deleted",
            {"id": "sub_rp", "customer": "cus_rp"},
        )
        p1, p2 = self._mock_replay(event)
        with p1, p2:
            resp = self.client.post(
                REPLAY_URL, data={"event_id": "evt_rp"},
                content_type="application/json", **self._auth(),
            )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["dry_run"])
        self.assertEqual(data["resolution"], "replayable")
        self.assertEqual(data["target"]["email"], "replay@test.com")
        self.assertEqual(data["transition"]["proposed"]["tier"], "free")
        user.refresh_from_db()
        self.assertEqual(user.tier.slug, "main", "Dry-run must not mutate.")

    def test_replay_confirmed_applies_and_records_operator_attempt(self):
        user = self._paid_user("confirm@test.com", "sub_cf", "cus_cf")
        event = _stripe_event(
            "evt_cf", "customer.subscription.deleted",
            {"id": "sub_cf", "customer": "cus_cf"},
        )
        p1, p2 = self._mock_replay(event)
        with p1, p2:
            resp = self.client.post(
                REPLAY_URL,
                data={
                    "event_id": "evt_cf",
                    "dry_run": False,
                    "confirm": "replay_cancellation_event",
                },
                content_type="application/json", **self._auth(),
            )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertFalse(data["dry_run"])
        self.assertEqual(data["outcome"], "processed")
        self.assertEqual(data["membership_before"]["tier"], "main")
        self.assertEqual(data["membership_after"]["tier"], "free")
        user.refresh_from_db()
        self.assertEqual(user.tier.slug, "free")
        attempt = StripeWebhookDeliveryAttempt.objects.get(
            stripe_event_id="evt_cf", source="operator_replay",
        )
        self.assertEqual(attempt.requested_by, self.admin)

    def test_replay_confirmed_is_idempotent(self):
        self._paid_user("idem@test.com", "sub_id", "cus_id")
        event = _stripe_event(
            "evt_id", "customer.subscription.deleted",
            {"id": "sub_id", "customer": "cus_id"},
        )
        body = {
            "event_id": "evt_id",
            "dry_run": False,
            "confirm": "replay_cancellation_event",
        }
        p1, p2 = self._mock_replay(event)
        with p1, p2:
            first = self.client.post(
                REPLAY_URL, data=body, content_type="application/json",
                **self._auth(),
            )
            self.assertEqual(first.status_code, 200)
            # Second confirmed request: now the terminal event exists, so
            # replay is rejected as already applied.
            second = self.client.post(
                REPLAY_URL, data=body, content_type="application/json",
                **self._auth(),
            )
        self.assertEqual(second.status_code, 422)
        self.assertEqual(second.json()["code"], "already_processed")

    def test_replay_rejections_write_nothing(self):
        # Unsupported event type.
        invoice = _stripe_event(
            "evt_inv", "invoice.payment_failed", {"id": "in_1", "customer": "cus_x"},
        )
        p1, p2 = self._mock_replay(invoice)
        with p1, p2:
            resp = self.client.post(
                REPLAY_URL, data={"event_id": "evt_inv"},
                content_type="application/json", **self._auth(),
            )
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["code"], "unsupported_event_type")

    def test_replay_mode_mismatch_rejected(self):
        test_mode_event = _stripe_event(
            "evt_tm", "customer.subscription.deleted",
            {"id": "sub_tm", "customer": "cus_tm"}, livemode=False,
        )
        p1, p2 = self._mock_replay(test_mode_event, secret="sk_live_abc")
        with p1, p2:
            resp = self.client.post(
                REPLAY_URL, data={"event_id": "evt_tm"},
                content_type="application/json", **self._auth(),
            )
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["code"], "mode_mismatch")

    def test_replay_already_processed_rejected_on_write(self):
        self._paid_user("ap@test.com", "sub_ap", "cus_ap")
        WebhookEvent.objects.create(
            stripe_event_id="evt_ap", event_type="customer.subscription.deleted",
            status=WebhookEvent.STATUS_PROCESSED,
        )
        event = _stripe_event(
            "evt_ap", "customer.subscription.deleted",
            {"id": "sub_ap", "customer": "cus_ap"},
        )
        p1, p2 = self._mock_replay(event)
        with p1, p2:
            dry = self.client.post(
                REPLAY_URL, data={"event_id": "evt_ap"},
                content_type="application/json", **self._auth(),
            )
            self.assertEqual(dry.json()["resolution"], "already_processed")
            write = self.client.post(
                REPLAY_URL,
                data={
                    "event_id": "evt_ap",
                    "dry_run": False,
                    "confirm": "replay_cancellation_event",
                },
                content_type="application/json", **self._auth(),
            )
        self.assertEqual(write.status_code, 422)
        self.assertEqual(write.json()["code"], "already_processed")

    def test_replay_missing_event_id_422(self):
        resp = self.client.post(
            REPLAY_URL, data={}, content_type="application/json", **self._auth(),
        )
        self.assertEqual(resp.status_code, 422)
