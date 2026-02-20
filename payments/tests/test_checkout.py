"""Tests for the Stripe checkout and subscription management views.

Tests cover:
- POST /api/checkout/create (create Stripe Checkout session)
- POST /api/subscription/upgrade
- POST /api/subscription/downgrade
- POST /api/subscription/cancel
- Authentication requirements
- Input validation
"""

import json
from unittest.mock import MagicMock, patch

from django.test import TestCase

from accounts.models import User
from payments.models import Tier


class CreateCheckoutViewTest(TestCase):
    """Tests for the create_checkout view (POST /api/checkout/create)."""

    def setUp(self):
        self.user = User.objects.create_user(
            email="checkout@test.com", password="testpass123"
        )
        self.basic_tier = Tier.objects.get(slug="basic")
        self.basic_tier.stripe_price_id_monthly = "price_basic_monthly"
        self.basic_tier.stripe_price_id_yearly = "price_basic_yearly"
        self.basic_tier.save()

    def test_requires_authentication(self):
        """Unauthenticated requests are redirected to login."""
        response = self.client.post(
            "/api/checkout/create",
            data=json.dumps({"tier_slug": "basic", "billing_period": "monthly"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response.url)

    def test_requires_post_method(self):
        """GET requests return 405."""
        self.client.login(email="checkout@test.com", password="testpass123")
        response = self.client.get("/api/checkout/create")
        self.assertEqual(response.status_code, 405)

    def test_requires_tier_slug(self):
        """Missing tier_slug returns 400."""
        self.client.login(email="checkout@test.com", password="testpass123")
        response = self.client.post(
            "/api/checkout/create",
            data=json.dumps({"billing_period": "monthly"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("tier_slug", response.json()["error"])

    def test_invalid_billing_period_returns_400(self):
        """Invalid billing_period returns 400."""
        self.client.login(email="checkout@test.com", password="testpass123")
        response = self.client.post(
            "/api/checkout/create",
            data=json.dumps({"tier_slug": "basic", "billing_period": "weekly"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("billing_period", response.json()["error"])

    def test_invalid_json_returns_400(self):
        """Non-JSON body returns 400."""
        self.client.login(email="checkout@test.com", password="testpass123")
        response = self.client.post(
            "/api/checkout/create",
            data="not json",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_nonexistent_tier_returns_400(self):
        """Requesting a tier that doesn't exist returns 400."""
        self.client.login(email="checkout@test.com", password="testpass123")
        response = self.client.post(
            "/api/checkout/create",
            data=json.dumps({"tier_slug": "nonexistent", "billing_period": "monthly"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    @patch("payments.views.checkout.create_checkout_session")
    def test_returns_checkout_url_on_success(self, mock_create):
        """Successful checkout creation returns a checkout_url."""
        mock_session = MagicMock()
        mock_session.url = "https://checkout.stripe.com/pay/cs_test_123"
        mock_create.return_value = mock_session

        self.client.login(email="checkout@test.com", password="testpass123")
        response = self.client.post(
            "/api/checkout/create",
            data=json.dumps({"tier_slug": "basic", "billing_period": "monthly"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("checkout_url", data)
        self.assertEqual(data["checkout_url"], "https://checkout.stripe.com/pay/cs_test_123")

    @patch("payments.views.checkout.create_checkout_session")
    def test_passes_correct_params_monthly(self, mock_create):
        """Monthly billing uses the correct parameters."""
        mock_session = MagicMock()
        mock_session.url = "https://checkout.stripe.com/pay/cs_test"
        mock_create.return_value = mock_session

        self.client.login(email="checkout@test.com", password="testpass123")
        self.client.post(
            "/api/checkout/create",
            data=json.dumps({"tier_slug": "basic", "billing_period": "monthly"}),
            content_type="application/json",
        )

        mock_create.assert_called_once()
        call_kwargs = mock_create.call_args[1]
        self.assertEqual(call_kwargs["tier_slug"], "basic")
        self.assertEqual(call_kwargs["billing_period"], "monthly")

    @patch("payments.views.checkout.create_checkout_session")
    def test_passes_correct_params_yearly(self, mock_create):
        """Yearly billing uses the correct parameters."""
        mock_session = MagicMock()
        mock_session.url = "https://checkout.stripe.com/pay/cs_test"
        mock_create.return_value = mock_session

        self.client.login(email="checkout@test.com", password="testpass123")
        self.client.post(
            "/api/checkout/create",
            data=json.dumps({"tier_slug": "basic", "billing_period": "yearly"}),
            content_type="application/json",
        )

        mock_create.assert_called_once()
        call_kwargs = mock_create.call_args[1]
        self.assertEqual(call_kwargs["billing_period"], "yearly")


class UpgradeViewTest(TestCase):
    """Tests for the upgrade view (POST /api/subscription/upgrade)."""

    def setUp(self):
        self.user = User.objects.create_user(
            email="upgrade@test.com", password="testpass123"
        )
        self.user.subscription_id = "sub_test"
        self.user.save(update_fields=["subscription_id"])

    def test_requires_authentication(self):
        response = self.client.post(
            "/api/subscription/upgrade",
            data=json.dumps({"tier_slug": "main"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 302)

    def test_requires_post_method(self):
        self.client.login(email="upgrade@test.com", password="testpass123")
        response = self.client.get("/api/subscription/upgrade")
        self.assertEqual(response.status_code, 405)

    def test_requires_tier_slug(self):
        self.client.login(email="upgrade@test.com", password="testpass123")
        response = self.client.post(
            "/api/subscription/upgrade",
            data=json.dumps({"billing_period": "monthly"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    @patch("payments.views.checkout.upgrade_subscription")
    def test_returns_ok_on_success(self, mock_upgrade):
        """Successful upgrade returns status ok."""
        mock_upgrade.return_value = MagicMock()

        self.client.login(email="upgrade@test.com", password="testpass123")
        response = self.client.post(
            "/api/subscription/upgrade",
            data=json.dumps({"tier_slug": "main", "billing_period": "monthly"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    @patch("payments.views.checkout.upgrade_subscription")
    def test_no_subscription_returns_400(self, mock_upgrade):
        """User with no subscription gets 400."""
        mock_upgrade.side_effect = ValueError("User has no active subscription to upgrade.")

        self.client.login(email="upgrade@test.com", password="testpass123")
        response = self.client.post(
            "/api/subscription/upgrade",
            data=json.dumps({"tier_slug": "main", "billing_period": "monthly"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)


class DowngradeViewTest(TestCase):
    """Tests for the downgrade view (POST /api/subscription/downgrade)."""

    def setUp(self):
        self.user = User.objects.create_user(
            email="downgrade@test.com", password="testpass123"
        )
        self.user.subscription_id = "sub_test"
        self.user.save(update_fields=["subscription_id"])

    def test_requires_authentication(self):
        response = self.client.post(
            "/api/subscription/downgrade",
            data=json.dumps({"tier_slug": "basic"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 302)

    def test_requires_tier_slug(self):
        self.client.login(email="downgrade@test.com", password="testpass123")
        response = self.client.post(
            "/api/subscription/downgrade",
            data=json.dumps({"billing_period": "monthly"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    @patch("payments.views.checkout.downgrade_subscription")
    def test_returns_ok_on_success(self, mock_downgrade):
        """Successful downgrade returns status ok."""
        mock_downgrade.return_value = MagicMock()

        self.client.login(email="downgrade@test.com", password="testpass123")
        response = self.client.post(
            "/api/subscription/downgrade",
            data=json.dumps({"tier_slug": "basic", "billing_period": "monthly"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    @patch("payments.views.checkout.downgrade_subscription")
    def test_sets_pending_tier(self, mock_downgrade):
        """Downgrade service sets pending_tier on the user."""
        # Test the service directly instead (the view calls the service)
        from payments.services import downgrade_subscription

        basic_tier = Tier.objects.get(slug="basic")
        basic_tier.stripe_price_id_monthly = "price_basic_monthly"
        basic_tier.save()

        main_tier = Tier.objects.get(slug="main")
        user = User.objects.create_user(email="penddown@test.com")
        user.tier = main_tier
        user.subscription_id = "sub_penddown"
        user.save(update_fields=["tier", "subscription_id"])

        # Mock the Stripe client
        with patch("payments.services._get_stripe_client") as mock_client:
            mock_sub = MagicMock()
            mock_sub.items.data = [MagicMock(id="si_item_1")]
            mock_client.return_value.subscriptions.retrieve.return_value = mock_sub
            mock_client.return_value.subscriptions.update.return_value = mock_sub

            downgrade_subscription(user, "basic", "monthly")

        user.refresh_from_db()
        self.assertEqual(user.pending_tier, basic_tier)


class CancelViewTest(TestCase):
    """Tests for the cancel view (POST /api/subscription/cancel)."""

    def setUp(self):
        self.user = User.objects.create_user(
            email="cancel@test.com", password="testpass123"
        )
        self.user.subscription_id = "sub_cancel"
        self.user.save(update_fields=["subscription_id"])

    def test_requires_authentication(self):
        response = self.client.post("/api/subscription/cancel")
        self.assertEqual(response.status_code, 302)

    def test_requires_post_method(self):
        self.client.login(email="cancel@test.com", password="testpass123")
        response = self.client.get("/api/subscription/cancel")
        self.assertEqual(response.status_code, 405)

    @patch("payments.views.checkout.cancel_subscription")
    def test_returns_ok_on_success(self, mock_cancel):
        """Successful cancellation returns status ok."""
        mock_cancel.return_value = MagicMock()

        self.client.login(email="cancel@test.com", password="testpass123")
        response = self.client.post("/api/subscription/cancel")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    @patch("payments.views.checkout.cancel_subscription")
    def test_no_subscription_returns_400(self, mock_cancel):
        """User with no subscription gets 400."""
        mock_cancel.side_effect = ValueError("User has no active subscription to cancel.")

        self.client.login(email="cancel@test.com", password="testpass123")
        response = self.client.post("/api/subscription/cancel")
        self.assertEqual(response.status_code, 400)
