from django.test import TestCase, tag

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
    """Smoke + context tests for the /pricing page.

    The visible tier cards, prices, monthly/annual toggle, CTA buttons,
    "Most Popular" badge, free-tier-links-to-newsletter behavior, and
    stripe payment links are all exercised end-to-end by
    `playwright_tests/test_membership_tiers.py` (30+ scenarios). Only
    the view-context invariants and a 200/template smoke test stay at
    the Django layer (#261).
    """

    def test_pricing_page_smoke(self):
        response = self.client.get("/pricing")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "payments/pricing.html")

    def test_pricing_page_has_four_tier_cards(self):
        response = self.client.get("/pricing")
        self.assertEqual(len(response.context["tiers_data"]), 4)

    def test_pricing_page_context_tiers_ordered_by_level(self):
        response = self.client.get("/pricing")
        tiers_data = response.context["tiers_data"]
        levels = [item["tier"].level for item in tiers_data]
        self.assertEqual(levels, [0, 10, 20, 30])

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
