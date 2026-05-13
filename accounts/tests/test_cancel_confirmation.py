"""Regression tests for removing local cancel confirmation flow."""

from django.test import TestCase, override_settings

from accounts.models import User
from payments.models import Tier


@override_settings(STRIPE_CUSTOMER_PORTAL_URL="https://billing.example.test/portal")
class CustomerPortalBillingManagementTest(TestCase):
    """Billing changes are delegated to Stripe Customer Portal."""

    def test_paid_member_sees_portal_link_without_local_cancel_controls(self):
        main_tier = Tier.objects.get(slug="main")
        user = User.objects.create_user(email="portal-billing@test.com")
        user.tier = main_tier
        user.subscription_id = "sub_portal"
        user.save(update_fields=["tier", "subscription_id"])
        self.client.force_login(user)

        response = self.client.get("/account/")

        self.assertContains(response, 'id="manage-subscription-btn"')
        self.assertContains(response, "https://billing.example.test/portal")
        self.assertNotContains(response, 'id="cancel-btn"')
        self.assertNotContains(response, 'id="cancel-modal"')
        self.assertNotContains(response, "/account/api/cancel")

    def test_account_cancel_api_is_removed(self):
        user = User.objects.create_user(email="removed-cancel-api@test.com")
        self.client.force_login(user)

        response = self.client.post(
            "/account/api/cancel",
            data="{}",
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 404)
