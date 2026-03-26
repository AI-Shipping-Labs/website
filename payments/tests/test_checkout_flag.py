"""Tests for the STRIPE_CHECKOUT_ENABLED feature flag (issue #154).

Covers:
- Checkout endpoints return 410 when flag is off
- Checkout endpoints work normally when flag is on
- Course purchase endpoint returns 410 when flag is off
- Pricing page context includes stripe_checkout_enabled
- Pricing page shows payment links when flag is off
- Pricing page shows Manage Subscription for paid users
- Prefilled email on payment links for logged-in users
- Account page shows Manage Subscription link for paid users
"""

import json
from unittest.mock import patch, MagicMock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from content.models import Course
from tests.fixtures import TierSetupMixin

User = get_user_model()


# ============================================================
# Checkout endpoints gated by flag (flag OFF = default)
# ============================================================


class CheckoutFlagOffTest(TierSetupMixin, TestCase):
    """When STRIPE_CHECKOUT_ENABLED is False (default), checkout endpoints return 410."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = User.objects.create_user(
            email="flagoff@test.com", password="testpass123"
        )

    def setUp(self):
        self.client.login(email="flagoff@test.com", password="testpass123")

    @override_settings(STRIPE_CHECKOUT_ENABLED=False)
    def test_create_checkout_returns_410(self):
        response = self.client.post(
            "/api/checkout/create",
            data=json.dumps({"tier_slug": "basic", "billing_period": "monthly"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 410)
        data = response.json()
        self.assertIn("payment links", data["error"])
        self.assertIn("portal_url", data)

    @override_settings(STRIPE_CHECKOUT_ENABLED=False)
    def test_upgrade_returns_410(self):
        response = self.client.post(
            "/api/subscription/upgrade",
            data=json.dumps({"tier_slug": "main", "billing_period": "monthly"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 410)
        data = response.json()
        self.assertIn("payment links", data["error"])

    @override_settings(STRIPE_CHECKOUT_ENABLED=False)
    def test_downgrade_returns_410(self):
        response = self.client.post(
            "/api/subscription/downgrade",
            data=json.dumps({"tier_slug": "basic", "billing_period": "monthly"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 410)
        data = response.json()
        self.assertIn("payment links", data["error"])

    @override_settings(STRIPE_CHECKOUT_ENABLED=False)
    def test_cancel_returns_410(self):
        response = self.client.post(
            "/api/subscription/cancel",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 410)
        data = response.json()
        self.assertIn("payment links", data["error"])


# ============================================================
# Checkout endpoints work when flag is ON
# ============================================================


class CheckoutFlagOnTest(TierSetupMixin, TestCase):
    """When STRIPE_CHECKOUT_ENABLED is True, checkout endpoints proceed normally."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = User.objects.create_user(
            email="flagon@test.com", password="testpass123"
        )

    def setUp(self):
        self.client.login(email="flagon@test.com", password="testpass123")

    @override_settings(STRIPE_CHECKOUT_ENABLED=True)
    @patch("payments.views.checkout.create_checkout_session")
    def test_create_checkout_proceeds_when_flag_on(self, mock_create):
        mock_session = MagicMock()
        mock_session.url = "https://checkout.stripe.com/test"
        mock_create.return_value = mock_session

        response = self.client.post(
            "/api/checkout/create",
            data=json.dumps({"tier_slug": "basic", "billing_period": "monthly"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["checkout_url"], "https://checkout.stripe.com/test")

    @override_settings(STRIPE_CHECKOUT_ENABLED=True)
    @patch("payments.views.checkout.upgrade_subscription")
    def test_upgrade_proceeds_when_flag_on(self, mock_upgrade):
        response = self.client.post(
            "/api/subscription/upgrade",
            data=json.dumps({"tier_slug": "main", "billing_period": "monthly"}),
            content_type="application/json",
        )
        # Should not be 410; it proceeds to normal logic
        self.assertNotEqual(response.status_code, 410)
        mock_upgrade.assert_called_once()

    @override_settings(STRIPE_CHECKOUT_ENABLED=True)
    @patch("payments.views.checkout.downgrade_subscription")
    def test_downgrade_proceeds_when_flag_on(self, mock_downgrade):
        response = self.client.post(
            "/api/subscription/downgrade",
            data=json.dumps({"tier_slug": "basic", "billing_period": "monthly"}),
            content_type="application/json",
        )
        self.assertNotEqual(response.status_code, 410)
        mock_downgrade.assert_called_once()

    @override_settings(STRIPE_CHECKOUT_ENABLED=True)
    @patch("payments.views.checkout.cancel_subscription")
    def test_cancel_proceeds_when_flag_on(self, mock_cancel):
        response = self.client.post(
            "/api/subscription/cancel",
            content_type="application/json",
        )
        self.assertNotEqual(response.status_code, 410)
        mock_cancel.assert_called_once()


# ============================================================
# Course purchase endpoint gated by flag
# ============================================================


class CoursePurchaseFlagTest(TierSetupMixin, TestCase):
    """Course purchase endpoint respects STRIPE_CHECKOUT_ENABLED flag."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = User.objects.create_user(
            email="courseflag@test.com", password="testpass123"
        )
        cls.course = Course.objects.create(
            title="Test Course",
            slug="test-course",
            status="published",
            required_level=0,
            individual_price_eur=29,
            stripe_price_id="price_test_course",
        )

    def setUp(self):
        self.client.login(email="courseflag@test.com", password="testpass123")

    @override_settings(STRIPE_CHECKOUT_ENABLED=False)
    def test_course_purchase_returns_410_when_flag_off(self):
        response = self.client.post(
            f"/api/courses/{self.course.slug}/purchase",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 410)
        data = response.json()
        self.assertIn("payment links", data["error"])
        self.assertIn("portal_url", data)


# ============================================================
# Pricing page context and template
# ============================================================


class PricingPageFlagOffTest(TierSetupMixin, TestCase):
    """Pricing page when STRIPE_CHECKOUT_ENABLED is False."""

    @override_settings(STRIPE_CHECKOUT_ENABLED=False)
    def test_context_includes_stripe_checkout_enabled_false(self):
        response = self.client.get("/pricing")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["stripe_checkout_enabled"])

    @override_settings(STRIPE_CHECKOUT_ENABLED=False)
    def test_anonymous_user_sees_payment_links(self):
        response = self.client.get("/pricing")
        self.assertEqual(response.status_code, 200)
        # Payment links should be in the page (from settings.STRIPE_PAYMENT_LINKS)
        self.assertContains(response, "buy.stripe.com")

    @override_settings(STRIPE_CHECKOUT_ENABLED=False)
    def test_anonymous_user_no_prefilled_email(self):
        response = self.client.get("/pricing")
        self.assertNotContains(response, "prefilled_email")

    @override_settings(STRIPE_CHECKOUT_ENABLED=False)
    def test_logged_in_user_gets_prefilled_email(self):
        user = User.objects.create_user(email="prefill@test.com", password="testpass123")
        self.client.login(email="prefill@test.com", password="testpass123")
        response = self.client.get("/pricing")
        self.assertContains(response, "prefilled_email=prefill@test.com")

    @override_settings(STRIPE_CHECKOUT_ENABLED=False)
    def test_paid_user_sees_manage_subscription(self):
        user = User.objects.create_user(email="paid@test.com", password="testpass123")
        user.tier = self.main_tier
        user.save(update_fields=["tier"])
        self.client.login(email="paid@test.com", password="testpass123")
        response = self.client.get("/pricing")
        self.assertContains(response, "Manage Subscription")
        self.assertTrue(response.context["is_paid_member"])

    @override_settings(STRIPE_CHECKOUT_ENABLED=False)
    def test_free_user_does_not_see_manage_subscription(self):
        user = User.objects.create_user(email="free@test.com", password="testpass123")
        self.client.login(email="free@test.com", password="testpass123")
        response = self.client.get("/pricing")
        self.assertFalse(response.context["is_paid_member"])

    @override_settings(STRIPE_CHECKOUT_ENABLED=False)
    def test_payment_links_have_monthly_and_annual_data(self):
        """Tier CTA links have data attributes for monthly/annual toggle."""
        response = self.client.get("/pricing")
        self.assertContains(response, "data-link-monthly")
        self.assertContains(response, "data-link-annual")


class PricingPageFlagOnTest(TierSetupMixin, TestCase):
    """Pricing page when STRIPE_CHECKOUT_ENABLED is True."""

    @override_settings(STRIPE_CHECKOUT_ENABLED=True)
    def test_context_includes_stripe_checkout_enabled_true(self):
        response = self.client.get("/pricing")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["stripe_checkout_enabled"])

    @override_settings(STRIPE_CHECKOUT_ENABLED=True)
    def test_no_prefilled_email_when_checkout_enabled(self):
        """When checkout is enabled, payment links are not used so no prefilled_email."""
        user = User.objects.create_user(email="nopre@test.com", password="testpass123")
        self.client.login(email="nopre@test.com", password="testpass123")
        response = self.client.get("/pricing")
        self.assertNotContains(response, "prefilled_email")


# ============================================================
# Account page - Manage Subscription link
# ============================================================


class AccountPageManageSubscriptionTest(TierSetupMixin, TestCase):
    """Account page shows Manage Subscription link for paid users."""

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

    def test_paid_user_sees_manage_subscription(self):
        self.client.login(email="paidacct@test.com", password="testpass123")
        response = self.client.get("/account/")
        self.assertContains(response, "Manage Subscription")
        self.assertContains(response, "manage-subscription-btn")

    def test_free_user_does_not_see_manage_subscription(self):
        self.client.login(email="freeacct@test.com", password="testpass123")
        response = self.client.get("/account/")
        self.assertNotContains(response, "manage-subscription-btn")
