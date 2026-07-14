"""Current Stripe product-model tests.

Pricing uses Payment Links, paid members use Customer Portal, and the
legacy course-purchase API returns 410 unconditionally.
"""

from urllib.parse import parse_qs, urlparse

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase

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

    def test_course_purchase_endpoint_returns_410(self):
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

    def test_anonymous_user_sees_payment_links(self):
        response = self.client.get("/pricing")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "/api/checkout/create")

        tiers_data = {
            item["tier"].slug: item
            for item in response.context["tiers_data"]
        }
        for tier_slug, links in settings.STRIPE_PAYMENT_LINKS.items():
            with self.subTest(tier=tier_slug):
                item = tiers_data[tier_slug]
                self.assertEqual(item["payment_link_monthly"], links["monthly"])
                self.assertEqual(item["payment_link_annual"], links["annual"])
                for period in ("monthly", "annual"):
                    query = parse_qs(
                        urlparse(item[f"payment_link_{period}"]).query
                    )
                    self.assertNotIn("client_reference_id", query)
                    self.assertNotIn("locked_prefilled_email", query)
                    self.assertNotIn("prefilled_email", query)
                self.assertContains(
                    response,
                    f'data-link-monthly="{links["monthly"]}"',
                )
                self.assertContains(
                    response,
                    f'data-link-annual="{links["annual"]}"',
                )
                self.assertContains(response, f'href="{links["annual"]}"')

    def test_logged_in_user_gets_local_post_targets_without_identity_query(self):
        user = User.objects.create_user(
            email="prefill+stripe@test.com",
            password="testpass123",
        )
        self.client.login(email="prefill+stripe@test.com", password="testpass123")
        response = self.client.get("/pricing")

        tiers_data = {
            item["tier"].slug: item
            for item in response.context["tiers_data"]
        }
        for tier_slug in settings.STRIPE_PAYMENT_LINKS:
            item = tiers_data[tier_slug]
            for period in ("monthly", "annual"):
                with self.subTest(tier=tier_slug, period=period):
                    link = item[f"payment_link_{period}"]
                    self.assertEqual(
                        link,
                        f"/payments/checkout/{tier_slug}/{period}",
                    )
                    self.assertNotIn(str(user.pk), link)
                    self.assertNotIn(user.email, link)
                    if period == "annual":
                        self.assertContains(response, f'action="{link}"')
                    else:
                        self.assertContains(response, f'data-link-monthly="{link}"')

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
