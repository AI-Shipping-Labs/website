"""Tests for hard-deprecated local Stripe checkout APIs."""

import json
from unittest.mock import patch

from django.test import TestCase, override_settings, tag

from accounts.models import User


@tag("core")
class DeprecatedCheckoutViewsTest(TestCase):
    """Local checkout/subscription mutation endpoints no longer call Stripe."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email="deprecated-checkout@test.com",
            password="testpass123",
        )

    def setUp(self):
        self.client.login(
            email="deprecated-checkout@test.com",
            password="testpass123",
        )

    @override_settings(
        STRIPE_CHECKOUT_ENABLED=True,
        STRIPE_CUSTOMER_PORTAL_URL="https://billing.example.test/portal",
    )
    @patch("payments.views.checkout.get_config")
    def test_checkout_and_subscription_mutation_endpoints_return_410(self, mock_config):
        mock_config.return_value = "https://billing.example.test/portal"
        endpoints = [
            (
                "/api/checkout/create",
                {"tier_slug": "basic", "billing_period": "monthly"},
            ),
            (
                "/api/subscription/upgrade",
                {"tier_slug": "main", "billing_period": "monthly"},
            ),
            (
                "/api/subscription/downgrade",
                {"tier_slug": "basic", "billing_period": "monthly"},
            ),
            ("/api/subscription/cancel", {}),
        ]

        for url, payload in endpoints:
            with self.subTest(url=url):
                response = self.client.post(
                    url,
                    data=json.dumps(payload),
                    content_type="application/json",
                )
                self.assertEqual(response.status_code, 410)
                data = response.json()
                self.assertIn("deprecated", data["error"])
                self.assertEqual(
                    data["portal_url"],
                    "https://billing.example.test/portal",
                )

    def test_deprecated_endpoints_still_require_authentication(self):
        self.client.logout()
        response = self.client.post(
            "/api/checkout/create",
            data=json.dumps({"tier_slug": "basic", "billing_period": "monthly"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response.url)
