"""Studio Stripe webhook diagnostics page (issue #1314)."""

from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase, tag
from django.urls import reverse

from accounts.models import User
from payments.models import (
    StripeWebhookDeliveryAttempt,
    StripeWebhookEndpointCheck,
    Tier,
)

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


@tag("core")
class StripeWebhooksStudioTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff@test.com", password="x", is_staff=True,
        )
        cls.member = User.objects.create_user(
            email="member@test.com", password="x",
        )
        cls.url = reverse("studio_stripe_webhooks")
        cls.verify_url = reverse("studio_stripe_webhooks_verify")

    def test_non_staff_denied(self):
        self.client.force_login(self.member)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 403)

    def test_anonymous_redirected_to_login(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/accounts/login/", resp["Location"])

    def test_dashboard_renders_with_verify_button(self):
        self.client.force_login(self.staff)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Verify Stripe configuration")
        self.assertContains(resp, EXPECTED_URL)
        # Quiet-but-healthy neutral empty state, not a failure.
        self.assertContains(resp, "No delivery has arrived yet")
        self.assertContains(resp, "not proof of failure")

    def test_verify_action_persists_check_and_redirects(self):
        self.client.force_login(self.staff)
        client = SimpleNamespace(
            webhook_endpoints=SimpleNamespace(
                list=lambda params=None: SimpleNamespace(data=[{
                    "id": "we_1", "url": EXPECTED_URL, "status": "enabled",
                    "enabled_events": ALL_EVENTS, "livemode": True,
                    "api_version": "2024-01-01",
                }]),
            )
        )

        def fake_config(key, default=""):
            if key == "STRIPE_SECRET_KEY":
                return "sk_live_abc"
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
            resp = self.client.post(self.verify_url)
        self.assertEqual(resp.status_code, 302)
        check = StripeWebhookEndpointCheck.objects.get()
        self.assertEqual(check.status, "pass")
        self.assertEqual(check.checked_by, self.staff)

    def test_delivery_rows_and_cancellation_filter(self):
        StripeWebhookDeliveryAttempt.objects.create(
            stripe_event_id="evt_c", event_type="customer.subscription.deleted",
            stripe_customer_id="cus_1", stripe_subscription_id="sub_1",
            outcome="processed", http_status=200,
        )
        StripeWebhookDeliveryAttempt.objects.create(
            stripe_event_id="evt_o", event_type="checkout.session.completed",
            outcome="processed", http_status=200,
        )
        self.client.force_login(self.staff)
        resp = self.client.get(self.url, {"scope": "cancellation"})
        self.assertContains(resp, "evt_c")
        self.assertNotContains(resp, "evt_o")

    def test_all_handled_events_shows_async_checkout_attempts(self):
        StripeWebhookDeliveryAttempt.objects.create(
            stripe_event_id="evt_async_failed",
            event_type="checkout.session.async_payment_failed",
            stripe_object_id="cs_async_failed",
            outcome="processed",
            http_status=200,
        )
        self.client.force_login(self.staff)
        response = self.client.get(self.url, {"scope": "all"})
        self.assertContains(response, "evt_async_failed")
        self.assertContains(response, "checkout.session.async_payment_failed")

    def test_inspect_event_is_read_only_preview(self):
        user = User.objects.create_user(email="target@test.com")
        user.tier = Tier.objects.get(slug="main")
        user.subscription_id = "sub_ins"
        user.stripe_customer_id = "cus_ins"
        user.save(update_fields=["tier", "subscription_id", "stripe_customer_id"])

        event = {
            "id": "evt_ins", "type": "customer.subscription.deleted",
            "livemode": True,
            "data": {"object": {"id": "sub_ins", "customer": "cus_ins"}},
        }
        client = SimpleNamespace(
            events=SimpleNamespace(retrieve=lambda eid: event),
        )

        def fake_config(key, default=""):
            return default

        self.client.force_login(self.staff)
        with patch(
            "payments.services.webhook_replay._get_stripe_client",
            return_value=client,
        ), patch(
            "payments.services.webhook_replay.get_config",
            side_effect=fake_config,
        ):
            resp = self.client.get(self.url, {"inspect_event_id": "evt_ins"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "target@test.com")
        self.assertContains(resp, "Proposed transition")
        # Studio never executes a replay.
        self.assertContains(resp, "Studio does not execute replays")
        user.refresh_from_db()
        self.assertEqual(user.tier.slug, "main", "Preview must not mutate.")
