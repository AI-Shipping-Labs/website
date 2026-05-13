"""Current Stripe product-model tests.

The checkout flag is intentionally ignored for local Checkout Session
creation. Pricing uses Payment Links, paid members use Customer Portal,
and legacy mutation APIs return 410.
"""

import json

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from content.models import Course
from tests.fixtures import TierSetupMixin

User = get_user_model()


class DeprecatedCheckoutEndpointsTest(TierSetupMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = User.objects.create_user(
            email="flagoff@test.com", password="testpass123"
        )
        cls.course = Course.objects.create(
            title="Test Course",
            slug="test-course",
            status="published",
            required_level=10,
            individual_price_eur=29,
            stripe_price_id="price_test_course",
        )

    def setUp(self):
        self.client.login(email="flagoff@test.com", password="testpass123")

    @override_settings(STRIPE_CHECKOUT_ENABLED=True)
    def test_membership_checkout_endpoints_return_410_even_when_flag_enabled(self):
        endpoints = [
            (
                "create",
                "/api/checkout/create",
                {"tier_slug": "basic", "billing_period": "monthly"},
            ),
            (
                "upgrade",
                "/api/subscription/upgrade",
                {"tier_slug": "main", "billing_period": "monthly"},
            ),
            (
                "downgrade",
                "/api/subscription/downgrade",
                {"tier_slug": "basic", "billing_period": "monthly"},
            ),
            ("cancel", "/api/subscription/cancel", {}),
        ]
        for label, url, payload in endpoints:
            with self.subTest(endpoint=label):
                response = self.client.post(
                    url,
                    data=json.dumps(payload),
                    content_type="application/json",
                )
                self.assertEqual(response.status_code, 410)
                self.assertIn("deprecated", response.json()["error"])

    @override_settings(STRIPE_CHECKOUT_ENABLED=True)
    def test_course_purchase_endpoint_returns_410_even_when_flag_enabled(self):
        response = self.client.post(
            f"/api/courses/{self.course.slug}/purchase",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 410)
        self.assertIn("deprecated", response.json()["error"])


class PricingPaymentLinksTest(TierSetupMixin, TestCase):
    """Pricing page always renders Payment Links."""

    def test_context_reports_local_checkout_disabled(self):
        response = self.client.get("/pricing")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["stripe_checkout_enabled"])

    @override_settings(STRIPE_CHECKOUT_ENABLED=True)
    def test_anonymous_user_sees_payment_links_even_when_checkout_flag_enabled(self):
        response = self.client.get("/pricing")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "buy.stripe.com")
        self.assertContains(response, "data-link-monthly")
        self.assertContains(response, "data-link-annual")
        self.assertNotContains(response, "/api/checkout/create")

    def test_logged_in_user_gets_url_encoded_prefilled_email(self):
        User.objects.create_user(
            email="prefill+stripe@test.com",
            password="testpass123",
        )
        self.client.login(email="prefill+stripe@test.com", password="testpass123")
        response = self.client.get("/pricing")

        self.assertContains(
            response,
            "prefilled_email=prefill%2Bstripe%40test.com",
        )

    def test_paid_user_upgrade_actions_use_customer_portal(self):
        user = User.objects.create_user(
            email="paid-pricing@test.com",
            password="testpass123",
        )
        user.tier = self.basic_tier
        user.subscription_id = "sub_basic"
        user.save(update_fields=["tier", "subscription_id"])
        self.client.login(email="paid-pricing@test.com", password="testpass123")

        response = self.client.get("/pricing")
        states = {
            item["tier"].slug: item["state"]
            for item in response.context["tiers_data"]
        }

        self.assertEqual(states["main"]["action_kind"], "portal")
        self.assertEqual(states["premium"]["action_kind"], "portal")


class AccountPageCustomerPortalTest(TierSetupMixin, TestCase):
    """Account page shows Customer Portal instead of local mutation controls."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.paid_user = User.objects.create_user(
            email="paidacct@test.com", password="testpass123"
        )
        cls.paid_user.tier = cls.main_tier
        cls.paid_user.subscription_id = "sub_test123"
        cls.paid_user.save(update_fields=["tier", "subscription_id"])

        cls.free_user = User.objects.create_user(
            email="freeacct@test.com", password="testpass123"
        )

    def test_paid_user_sees_manage_subscription_only(self):
        self.client.login(email="paidacct@test.com", password="testpass123")
        response = self.client.get("/account/")
        self.assertContains(response, "Manage Subscription")
        self.assertContains(response, "manage-subscription-btn")
        self.assertNotContains(response, "downgrade-btn")
        self.assertNotContains(response, "cancel-btn")

    def test_free_user_sees_pricing_upgrade_link(self):
        self.client.login(email="freeacct@test.com", password="testpass123")
        response = self.client.get("/account/")
        self.assertContains(response, 'id="upgrade-btn"')
        self.assertNotContains(response, "manage-subscription-btn")
