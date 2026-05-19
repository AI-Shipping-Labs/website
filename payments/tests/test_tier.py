from django.contrib.auth import get_user_model
from django.test import Client, TestCase, tag

from payments.models import Tier


@tag('core')
class TierModelTest(TestCase):
    """Tests for the Tier model bootstrap migration.

    Since #684, the seed migration only writes ``slug``, ``level``, and a
    placeholder ``name``. Every other editable field (prices, Stripe price
    IDs, description, features) is populated by the tiers.yaml content
    sync. The tests here cover the bootstrap-only invariants the migration
    is responsible for; price/feature/description coverage moved to the
    integrations test suite (``test_tiers_sync.py``).
    """

    def test_seed_migration_creates_four_tiers(self):
        """Data migration should seed exactly 4 tiers."""
        self.assertEqual(Tier.objects.count(), 4)

    def test_free_tier_exists(self):
        tier = Tier.objects.get(slug="free")
        self.assertEqual(tier.name, "Free")
        self.assertEqual(tier.level, 0)
        self.assertIsNone(tier.price_eur_month)
        self.assertIsNone(tier.price_eur_year)

    def test_basic_tier_bootstrap(self):
        tier = Tier.objects.get(slug="basic")
        self.assertEqual(tier.name, "Basic")
        self.assertEqual(tier.level, 10)

    def test_main_tier_bootstrap(self):
        tier = Tier.objects.get(slug="main")
        self.assertEqual(tier.name, "Main")
        self.assertEqual(tier.level, 20)

    def test_premium_tier_bootstrap(self):
        tier = Tier.objects.get(slug="premium")
        self.assertEqual(tier.name, "Premium")
        self.assertEqual(tier.level, 30)

    def test_tier_ordering_by_level(self):
        tiers = list(Tier.objects.values_list("slug", flat=True))
        self.assertEqual(tiers, ["free", "basic", "main", "premium"])

    def test_paid_tiers_have_no_prices_pre_sync(self):
        """Bootstrap migration does not seed prices; yaml sync populates them."""
        for slug in ("basic", "main", "premium"):
            tier = Tier.objects.get(slug=slug)
            self.assertIsNone(
                tier.price_eur_month,
                f"Tier {slug!r} should have no price before sync",
            )
            self.assertIsNone(
                tier.price_eur_year,
                f"Tier {slug!r} should have no price before sync",
            )

    def test_features_default_empty_list_pre_sync(self):
        """Bootstrap migration leaves features as the JSONField default ``[]``."""
        for tier in Tier.objects.all():
            self.assertEqual(
                tier.features, [],
                f"Tier {tier.slug!r} should have empty features pre-sync",
            )

    def test_description_empty_pre_sync(self):
        """Bootstrap migration leaves description as the field default ``''``."""
        for tier in Tier.objects.all():
            self.assertEqual(
                tier.description, "",
                f"Tier {tier.slug!r} should have empty description pre-sync",
            )

    def test_stripe_price_ids_empty_pre_sync(self):
        """Bootstrap migration leaves Stripe price IDs as the field default ``''``."""
        for tier in Tier.objects.all():
            self.assertEqual(tier.stripe_price_id_monthly, "")
            self.assertEqual(tier.stripe_price_id_yearly, "")


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

    def test_pricing_page_renders_seeded_tier_descriptions(self):
        """After yaml sync writes descriptions, the page must render them.

        Since #684 the bootstrap migration no longer seeds descriptions —
        tiers.yaml does. Simulate the post-sync state by writing
        descriptions directly to the Tier rows, then assert the pricing
        page picks them up.
        """
        sync_descriptions = {
            "free": "Free tier description after sync.",
            "basic": "Basic tier description after sync.",
            "main": "Main tier description after sync.",
            "premium": "Premium tier description after sync.",
        }
        for slug, desc in sync_descriptions.items():
            Tier.objects.filter(slug=slug).update(description=desc)

        response = self.client.get("/pricing")
        content = response.content.decode()
        for desc in sync_descriptions.values():
            self.assertIn(desc, content)


class TierPricingViewBootstrapTest(TestCase):
    """Operator-bootstrap path: fresh migrate, no content sync yet.

    The seed migration only writes slug/level/name placeholders. Pricing
    must still render 200 with all four tier names visible; prices and
    feature lists render blank because yaml sync has not run.

    Mirrors Playwright Scenario 3 in #684's spec at the Django layer.
    """

    def test_pricing_renders_200_with_tier_names_when_no_sync_has_run(self):
        # Mutate to a "no sync has run" state: empty descriptions/features.
        Tier.objects.all().update(
            description="",
            features=[],
            price_eur_month=None,
            price_eur_year=None,
        )
        response = self.client.get("/pricing")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        for tier_name in ("Free", "Basic", "Main", "Premium"):
            self.assertIn(tier_name, content)

    def test_bootstrap_tier_rows_have_no_undefined_name_string(self):
        """Regression: placeholder name must not leave 'undefined' on the page."""
        response = self.client.get("/pricing")
        self.assertNotIn("undefined", response.content.decode())


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


class TierAdminSmokeTest(TestCase):
    """Regression: TierAdmin change/add pages must render 200, not 500.

    Previously `prepopulated_fields = {"slug": ("name",)}` referenced
    `name`, which is in `readonly_fields` (yaml-managed). Django's admin
    raises `KeyError: "Key 'name' not found in 'TierForm'"` when a
    prepopulated source field is readonly, returning HTTP 500.
    """

    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.superuser = User.objects.create_superuser(
            email="tieradmin@test.com",
            password="pw",
        )

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.superuser)

    def test_change_page_returns_200(self):
        tier = Tier.objects.get(slug="basic")
        response = self.client.get(
            f"/admin/payments/tier/{tier.pk}/change/",
        )
        self.assertEqual(response.status_code, 200)

    def test_add_page_returns_200(self):
        response = self.client.get("/admin/payments/tier/add/")
        self.assertEqual(response.status_code, 200)

    def test_changelist_returns_200(self):
        response = self.client.get("/admin/payments/tier/")
        self.assertEqual(response.status_code, 200)
