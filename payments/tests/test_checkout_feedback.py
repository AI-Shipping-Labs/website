"""Tests for Stripe checkout success/cancelled redirect URLs (issue #114).

Backend behavior only. The success/cancelled banner HTML and the JS that toggles
its visibility are covered by Playwright (see follow-up issue #266 -- needs-testing
once Playwright coverage exists for `?checkout=success` and `?checkout=cancelled`).

The original template-string-matching tests (banner element id, CSS class strings,
`history.replaceState` substring checks) were removed under
`_docs/testing-guidelines.md` Rule 4 (do not test JavaScript by string-matching HTML).
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
