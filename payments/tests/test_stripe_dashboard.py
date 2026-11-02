from django.test import SimpleTestCase

from payments.stripe_dashboard import stripe_dashboard_url


class StripeDashboardURLTest(SimpleTestCase):
    def test_builds_account_scoped_resource_url(self):
        self.assertEqual(
            stripe_dashboard_url("acct_123", "customers", "cus_456"),
            "https://dashboard.stripe.com/acct_123/customers/cus_456",
        )

    def test_missing_account_or_object_returns_empty_url(self):
        self.assertEqual(stripe_dashboard_url("", "customers", "cus_456"), "")
        self.assertEqual(stripe_dashboard_url("acct_123", "customers", ""), "")
