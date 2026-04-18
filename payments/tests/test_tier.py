from django.test import TestCase, override_settings, tag

from payments.models import Tier


@tag('core')
class TierModelTest(TestCase):
    """Tests for the Tier model."""

    def test_seed_migration_creates_four_tiers(self):
        """Data migration should seed exactly 4 tiers."""
        self.assertEqual(Tier.objects.count(), 4)

    def test_free_tier_exists(self):
        tier = Tier.objects.get(slug="free")
        self.assertEqual(tier.name, "Free")
        self.assertEqual(tier.level, 0)
        self.assertIsNone(tier.price_eur_month)
        self.assertIsNone(tier.price_eur_year)

    def test_basic_tier_exists(self):
        tier = Tier.objects.get(slug="basic")
        self.assertEqual(tier.name, "Basic")
        self.assertEqual(tier.level, 10)
        self.assertEqual(tier.price_eur_month, 20)
        self.assertEqual(tier.price_eur_year, 200)

    def test_main_tier_exists(self):
        tier = Tier.objects.get(slug="main")
        self.assertEqual(tier.name, "Main")
        self.assertEqual(tier.level, 20)
        self.assertEqual(tier.price_eur_month, 50)
        self.assertEqual(tier.price_eur_year, 500)

    def test_premium_tier_exists(self):
        tier = Tier.objects.get(slug="premium")
        self.assertEqual(tier.name, "Premium")
        self.assertEqual(tier.level, 30)
        self.assertEqual(tier.price_eur_month, 100)
        self.assertEqual(tier.price_eur_year, 1000)

    def test_tier_ordering_by_level(self):
        tiers = list(Tier.objects.values_list("slug", flat=True))
        self.assertEqual(tiers, ["free", "basic", "main", "premium"])

    def test_features_is_list(self):
        tier = Tier.objects.get(slug="basic")
        self.assertIsInstance(tier.features, list)
        self.assertGreater(len(tier.features), 0)

    def test_free_tier_features(self):
        tier = Tier.objects.get(slug="free")
        self.assertIsInstance(tier.features, list)
        self.assertIn("Newsletter emails", tier.features)

    def test_description_not_empty(self):
        for tier in Tier.objects.all():
            self.assertTrue(
                len(tier.description) > 0,
                f"Tier '{tier.slug}' should have a description",
            )



class TierPricingViewTest(TestCase):
    """Tests for the /pricing page."""

    def test_pricing_page_returns_200(self):
        response = self.client.get("/pricing")
        self.assertEqual(response.status_code, 200)

    def test_pricing_page_uses_correct_template(self):
        response = self.client.get("/pricing")
        self.assertTemplateUsed(response, "payments/pricing.html")

    def test_pricing_page_contains_all_tier_names(self):
        response = self.client.get("/pricing")
        content = response.content.decode()
        self.assertIn("Free", content)
        self.assertIn("Basic", content)
        self.assertIn("Main", content)
        self.assertIn("Premium", content)

    def test_pricing_page_contains_most_popular_badge(self):
        response = self.client.get("/pricing")
        content = response.content.decode()
        self.assertIn("Most Popular", content)

    def test_pricing_page_contains_monthly_prices(self):
        response = self.client.get("/pricing")
        content = response.content.decode()
        # Monthly prices should be in data attributes
        self.assertIn('data-monthly="20"', content)
        self.assertIn('data-monthly="50"', content)
        self.assertIn('data-monthly="100"', content)

    def test_pricing_page_contains_annual_prices(self):
        response = self.client.get("/pricing")
        content = response.content.decode()
        # Annual prices should be in data attributes
        self.assertIn('data-annual="200"', content)
        self.assertIn('data-annual="500"', content)
        self.assertIn('data-annual="1000"', content)

    def test_pricing_page_free_tier_has_subscribe_button(self):
        response = self.client.get("/pricing")
        # Free tier CTA links to /#newsletter with "Subscribe" text
        self.assertContains(
            response,
            '<a href="/#newsletter"',
        )
        self.assertContains(response, "Subscribe")

    @override_settings(STRIPE_CHECKOUT_ENABLED=True)
    def test_pricing_page_paid_tiers_have_join_button(self):
        response = self.client.get("/pricing")
        content = response.content.decode()
        # Each paid tier has a CTA link with data-tier attribute
        self.assertIn('data-tier="basic"', content)
        self.assertIn('data-tier="main"', content)
        self.assertIn('data-tier="premium"', content)
        # CTA links use the tier-cta-link class
        self.assertGreaterEqual(content.count("tier-cta-link"), 3)

    def test_pricing_page_contains_billing_toggle(self):
        response = self.client.get("/pricing")
        content = response.content.decode()
        self.assertIn("billing-toggle", content)
        self.assertIn("Monthly", content)
        self.assertIn("Annual", content)

    def test_pricing_page_contains_tier_features(self):
        response = self.client.get("/pricing")
        content = response.content.decode()
        # Check some features from different tiers
        self.assertIn("Newsletter emails", content)
        self.assertIn("Exclusive articles", content)
        self.assertIn("Slack community access", content)
        self.assertIn("All mini-courses", content)

    def test_pricing_page_contains_stripe_payment_links(self):
        response = self.client.get("/pricing")
        content = response.content.decode()
        self.assertIn("buy.stripe.com", content)

    def test_pricing_page_has_four_tier_cards(self):
        response = self.client.get("/pricing")
        self.assertEqual(len(response.context["tiers_data"]), 4)

    def test_pricing_page_context_tiers_ordered_by_level(self):
        response = self.client.get("/pricing")
        tiers_data = response.context["tiers_data"]
        levels = [item["tier"].level for item in tiers_data]
        self.assertEqual(levels, [0, 10, 20, 30])

    def test_pricing_page_free_tier_no_payment_link(self):
        """Free tier Subscribe button should link to newsletter, not stripe."""
        response = self.client.get("/pricing")
        content = response.content.decode()
        # The free tier links to /#newsletter, not a stripe link
        self.assertIn('href="/#newsletter"', content)

    def test_pricing_page_contains_tier_descriptions(self):
        response = self.client.get("/pricing")
        content = response.content.decode()
        for item in response.context["tiers_data"]:
            self.assertIn(item["tier"].description, content)


class TierPricingViewAuthenticatedTest(TestCase):
    """Issue #238: logged-in users must reach `/pricing` and see all tier
    cards (the dashboard does not contain them, so this is the canonical
    destination for header/footer Membership links)."""

    @classmethod
    def setUpTestData(cls):
        from django.contrib.auth import get_user_model
        User = get_user_model()
        cls.user = User.objects.create_user(
            email="pricing-user@test.com",
            password="TestPass123!",
        )

    def setUp(self):
        self.client.force_login(self.user)

    def test_authenticated_pricing_page_returns_200(self):
        response = self.client.get("/pricing")
        self.assertEqual(response.status_code, 200)

    def test_authenticated_pricing_page_contains_all_tier_names(self):
        response = self.client.get("/pricing")
        content = response.content.decode()
        for tier_name in ("Free", "Basic", "Main", "Premium"):
            self.assertIn(tier_name, content)
