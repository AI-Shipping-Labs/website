"""Tests for checkout success/cancelled feedback banners -- issue #114.

Covers:
- Success redirect URL changed from /pricing?checkout=success to /?checkout=success
- Dashboard shows success banner HTML when ?checkout=success is in the URL
- Success banner is hidden by default (no query param)
- Pricing page shows cancelled banner HTML when ?checkout=cancelled is in the URL
- Cancelled banner is hidden by default (no query param)
- Success banner does not appear on pricing page
- Cancel banner does not appear on dashboard
- Dashboard renders correctly without query param (no empty banner space)
- Pricing page renders correctly without query param (no empty banner space)
"""

import json
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from payments.models import Tier

User = get_user_model()


@override_settings(STRIPE_CHECKOUT_ENABLED=True)
class CheckoutSuccessRedirectTest(TestCase):
    """Test that the checkout view redirects to /?checkout=success on success."""

    def setUp(self):
        self.user = User.objects.create_user(
            email="redirect@test.com", password="testpass123"
        )
        self.basic_tier = Tier.objects.get(slug="basic")
        self.basic_tier.stripe_price_id_monthly = "price_basic_monthly"
        self.basic_tier.stripe_price_id_yearly = "price_basic_yearly"
        self.basic_tier.save()

    @patch("payments.views.checkout.create_checkout_session")
    def test_success_url_points_to_dashboard(self, mock_create):
        """Success URL should be /?checkout=success (dashboard), not /pricing."""
        mock_session = MagicMock()
        mock_session.url = "https://checkout.stripe.com/pay/cs_test"
        mock_create.return_value = mock_session

        self.client.login(email="redirect@test.com", password="testpass123")
        self.client.post(
            "/api/checkout/create",
            data=json.dumps({"tier_slug": "basic", "billing_period": "monthly"}),
            content_type="application/json",
        )

        mock_create.assert_called_once()
        call_kwargs = mock_create.call_args[1]
        self.assertIn("/?checkout=success", call_kwargs["success_url"])
        self.assertNotIn("/pricing?checkout=success", call_kwargs["success_url"])

    @patch("payments.views.checkout.create_checkout_session")
    def test_cancel_url_points_to_pricing(self, mock_create):
        """Cancel URL should remain at /pricing?checkout=cancelled."""
        mock_session = MagicMock()
        mock_session.url = "https://checkout.stripe.com/pay/cs_test"
        mock_create.return_value = mock_session

        self.client.login(email="redirect@test.com", password="testpass123")
        self.client.post(
            "/api/checkout/create",
            data=json.dumps({"tier_slug": "basic", "billing_period": "monthly"}),
            content_type="application/json",
        )

        mock_create.assert_called_once()
        call_kwargs = mock_create.call_args[1]
        self.assertIn("/pricing?checkout=cancelled", call_kwargs["cancel_url"])


class DashboardSuccessBannerTest(TestCase):
    """Test the success banner on the dashboard after checkout."""

    def setUp(self):
        self.user = User.objects.create_user(
            email="success@test.com", password="testpass123",
            first_name="TestUser",
        )
        self.client.login(email="success@test.com", password="testpass123")

    def test_success_banner_html_present_with_query_param(self):
        """Dashboard contains the success banner HTML when ?checkout=success is present."""
        response = self.client.get("/?checkout=success")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("checkout-success-banner", content)
        self.assertIn("Payment successful! Welcome to AI Shipping Labs.", content)

    def test_success_banner_hidden_by_default(self):
        """The success banner element has the 'hidden' class by default (JS shows it)."""
        response = self.client.get("/?checkout=success")
        content = response.content.decode()
        # The banner div should contain the 'hidden' class in its HTML
        # (JavaScript removes it when the query param is present)
        self.assertIn('id="checkout-success-banner"', content)
        # The class attribute on the banner div includes 'hidden'
        self.assertIn('checkout-success-banner" class="mb-6 hidden', content)

    def test_success_banner_has_dismiss_button(self):
        """The success banner has a dismiss button."""
        response = self.client.get("/?checkout=success")
        content = response.content.decode()
        self.assertIn("dismiss-success-banner", content)

    def test_dashboard_without_param_has_no_visible_banner_space(self):
        """Dashboard renders correctly without ?checkout=success -- banner is hidden."""
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        # The banner HTML is present but hidden
        self.assertIn("checkout-success-banner", content)
        # The hidden class is there by default
        self.assertIn('class="mb-6 hidden', content)

    def test_dashboard_with_success_param_renders_normally(self):
        """Dashboard still shows all normal sections alongside the banner."""
        response = self.client.get("/?checkout=success")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("Welcome back", content)
        self.assertIn("Continue Learning", content)
        self.assertIn("Upcoming Events", content)

    def test_success_banner_javascript_present(self):
        """The dashboard includes JavaScript to show the success banner."""
        response = self.client.get("/?checkout=success")
        content = response.content.decode()
        self.assertIn("checkout-success-banner", content)
        self.assertIn("history.replaceState", content)

    def test_cancel_banner_not_on_dashboard(self):
        """The cancel banner should not appear on the dashboard."""
        response = self.client.get("/?checkout=cancelled")
        content = response.content.decode()
        self.assertNotIn("checkout-cancelled-banner", content)
        self.assertNotIn("Checkout was cancelled", content)


class PricingCancelledBannerTest(TestCase):
    """Test the cancelled banner on the pricing page after checkout cancellation."""

    def test_cancelled_banner_html_present_with_query_param(self):
        """Pricing page contains the cancelled banner HTML when ?checkout=cancelled is present."""
        response = self.client.get("/pricing?checkout=cancelled")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("checkout-cancelled-banner", content)
        self.assertIn("Checkout was cancelled. You can try again anytime.", content)

    def test_cancelled_banner_hidden_by_default(self):
        """The cancelled banner element has the 'hidden' class by default (JS shows it)."""
        response = self.client.get("/pricing?checkout=cancelled")
        content = response.content.decode()
        self.assertIn('id="checkout-cancelled-banner"', content)

    def test_cancelled_banner_has_dismiss_button(self):
        """The cancelled banner has a dismiss button."""
        response = self.client.get("/pricing?checkout=cancelled")
        content = response.content.decode()
        self.assertIn("dismiss-cancelled-banner", content)

    def test_pricing_without_param_has_no_visible_banner_space(self):
        """Pricing page renders correctly without ?checkout=cancelled -- banner is hidden."""
        response = self.client.get("/pricing")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        # The banner HTML is present but hidden
        self.assertIn("checkout-cancelled-banner", content)
        self.assertIn("hidden", content)

    def test_pricing_with_cancelled_param_renders_normally(self):
        """Pricing page still shows all tiers alongside the banner."""
        response = self.client.get("/pricing?checkout=cancelled")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("Choose your level of engagement", content)
        self.assertIn("Membership", content)

    def test_cancelled_banner_javascript_present(self):
        """The pricing page includes JavaScript to show the cancelled banner."""
        response = self.client.get("/pricing?checkout=cancelled")
        content = response.content.decode()
        self.assertIn("checkout-cancelled-banner", content)
        self.assertIn("history.replaceState", content)

    def test_success_banner_not_on_pricing_page(self):
        """The success banner should not appear on the pricing page."""
        response = self.client.get("/pricing?checkout=success")
        content = response.content.decode()
        self.assertNotIn("checkout-success-banner", content)
        self.assertNotIn("Payment successful", content)

    def test_pricing_page_anonymous_without_param(self):
        """Anonymous visitors see the pricing page normally without any banner visible."""
        response = self.client.get("/pricing")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("Choose your level of engagement", content)
        # Banner HTML is there but hidden
        self.assertIn('class="hidden', content)

    def test_pricing_page_anonymous_with_cancelled_param(self):
        """Anonymous visitors who cancelled checkout see the banner HTML."""
        response = self.client.get("/pricing?checkout=cancelled")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("Checkout was cancelled. You can try again anytime.", content)
