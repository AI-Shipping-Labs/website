"""Tests for the /membership free-tier CTA.

The free-tier card is a single "Join" button that links to the standalone
register page (with ?next=/membership). The inline register form that used to
embed here was retired — expanding it in place made the pricing page reflow
awkwardly. The authenticated branch never shows a signup CTA.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase

from content.models import SiteConfig
from payments.models import Tier


class PricingFreeTierJoinButtonTest(TestCase):
    """Anonymous visitors on /membership see a single Join button in the
    free tier's CTA slot. Logged-in users do not."""

    @classmethod
    def setUpTestData(cls):
        from pathlib import Path

        import yaml

        cls.User = get_user_model()
        cls.free = Tier.objects.get(slug="free")
        fixture_path = (
            Path(__file__).parents[2] / "content" / "tests" / "fixtures" / "tiers.yaml"
        )
        with open(fixture_path) as handle:
            tiers_data = yaml.safe_load(handle)
        SiteConfig.objects.create(key="tiers", data=tiers_data)

    def test_anonymous_pricing_shows_join_button_in_free_card(self):
        response = self.client.get("/membership")
        self.assertEqual(response.status_code, 200)
        # A single Join button links out to the register page.
        self.assertContains(response, 'data-testid="pricing-free-signup-cta"')
        self.assertContains(response, 'href="/accounts/register/?next=/membership"')
        # The retired inline register form must not render.
        self.assertNotContains(response, 'data-testid="inline-register-card"')
        self.assertNotContains(response, 'id="register-email"')
        self.assertNotContains(response, 'pricing-inline-register-embed')
        # Guard against Django comment leaks — multi-line ``{# #}`` tags
        # don't terminate so they leak into rendered HTML.
        self.assertNotContains(response, '{# ')

    def test_membership_explains_benefits_below_plan_cards(self):
        response = self.client.get("/membership")

        body = response.content.decode()
        self.assertNotContains(response, "Compare activities by tier")
        self.assertLess(body.index('id="pricing-section"'), body.index('id="activities"'))
        self.assertContains(response, 'data-testid="membership-benefit-row"', count=8)

    def test_authenticated_pricing_hides_free_signup_cta(self):
        """A logged-in user on /membership never sees the free-tier signup
        CTA. The free-tier card shifts to a disabled / current-plan
        state."""
        user = self.User.objects.create_user(
            email="auth-pricing@test.com", password="testpass123",
        )
        user.tier = self.free
        user.save(update_fields=["tier"])
        self.client.force_login(user)
        response = self.client.get("/membership")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-testid="pricing-free-signup-cta"')
        self.assertNotContains(response, 'data-testid="inline-register-card"')
        self.assertContains(response, 'data-testid="membership-benefits-section"')

    def test_pricing_mobile_indicator_controls_render_for_all_tiers(self):
        response = self.client.get("/membership")
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn('data-testid="pricing-tier-indicators"', body)
        self.assertEqual(
            body.count('data-testid="pricing-tier-indicator"'),
            4,
        )
        self.assertEqual(
            body.count('data-testid="pricing-tier-indicator-dot"'),
            4,
        )
        self.assertIn(
            'class="inline-flex min-h-[44px] min-w-[44px] shrink-0',
            body,
        )
        self.assertIn('data-tier-indicator-dot', body)
        for tier_name in ("Free", "Basic", "Main", "Premium"):
            self.assertIn(f'aria-label="Show {tier_name} tier"', body)

    def test_main_tier_uses_the_shared_most_popular_badge(self):
        response = self.client.get("/membership")
        body = response.content.decode()

        self.assertContains(
            response,
            'data-testid="pricing-most-popular-badge"',
            count=1,
        )
        self.assertContains(response, 'data-component="member-badge"')
        self.assertContains(response, 'Most popular', count=1)
        main_card = body.split('data-tier-card="main"', 1)[1].split(
            'data-tier-card="premium"', 1
        )[0]
        self.assertIn('data-testid="pricing-most-popular-badge"', main_card)
        self.assertIn('data-lucide="star"', main_card)
        badge_testid_index = main_card.index(
            'data-testid="pricing-most-popular-badge"'
        )
        badge_start = main_card.rfind('<span', 0, badge_testid_index)
        badge_opening = main_card[badge_start:main_card.index('>', badge_testid_index)]
        self.assertIn('bg-accent', badge_opening.split())
        self.assertIn('text-accent-foreground', badge_opening.split())
        self.assertNotIn('bg-accent/10', badge_opening.split())

    def test_cancelled_checkout_dismiss_has_accessible_target_and_focus_ring(self):
        response = self.client.get("/membership")
        body = response.content.decode()
        button = body.split('id="dismiss-cancelled-banner"', 1)[1].split(
            '</button>', 1
        )[0]

        for class_name in (
            'min-h-[44px]',
            'min-w-[44px]',
            'focus-visible:outline-none',
            'focus-visible:ring-2',
            'focus-visible:ring-accent',
            'focus-visible:ring-offset-2',
            'focus-visible:ring-offset-background',
        ):
            with self.subTest(class_name=class_name):
                self.assertIn(class_name, button)
        self.assertIn('aria-label="Dismiss"', body)
