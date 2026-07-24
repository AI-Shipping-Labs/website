"""Tests for the /pricing free-tier CTA.

The free-tier card is a single "Join" button that links to the standalone
register page (with ?next=/pricing). The inline register form that used to
embed here was retired — expanding it in place made the pricing page reflow
awkwardly. The authenticated branch never shows a signup CTA.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase

from payments.models import Tier


class PricingFreeTierJoinButtonTest(TestCase):
    """Anonymous visitors on /pricing see a single Join button in the
    free tier's CTA slot. Logged-in users do not."""

    @classmethod
    def setUpTestData(cls):
        cls.User = get_user_model()
        cls.free = Tier.objects.get(slug="free")

    def test_anonymous_pricing_shows_join_button_in_free_card(self):
        response = self.client.get("/pricing")
        self.assertEqual(response.status_code, 200)
        # A single Join button links out to the register page.
        self.assertContains(response, 'data-testid="pricing-free-signup-cta"')
        self.assertContains(response, 'href="/accounts/register/?next=/pricing"')
        # The retired inline register form must not render.
        self.assertNotContains(response, 'data-testid="inline-register-card"')
        self.assertNotContains(response, 'id="register-email"')
        self.assertNotContains(response, 'pricing-inline-register-embed')
        # Guard against Django comment leaks — multi-line ``{# #}`` tags
        # don't terminate so they leak into rendered HTML.
        self.assertNotContains(response, '{# ')

    def test_pricing_links_to_activities_by_tier_comparison(self):
        response = self.client.get("/pricing")

        self.assertContains(
            response,
            'href="/activities#access-by-tier"',
        )
        self.assertContains(response, "Compare activities by tier")

    def test_authenticated_pricing_hides_free_signup_cta(self):
        """A logged-in user on /pricing never sees the free-tier signup
        CTA. The free-tier card shifts to a disabled / current-plan
        state."""
        user = self.User.objects.create_user(
            email="auth-pricing@test.com", password="testpass123",
        )
        user.tier = self.free
        user.save(update_fields=["tier"])
        self.client.force_login(user)
        response = self.client.get("/pricing")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-testid="pricing-free-signup-cta"')
        self.assertNotContains(response, 'data-testid="inline-register-card"')
        self.assertContains(response, 'href="/activities#access-by-tier"')

    def test_pricing_mobile_indicator_controls_render_for_all_tiers(self):
        response = self.client.get("/pricing")
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn('data-testid="pricing-tier-indicators"', body)
        self.assertEqual(
            body.count('data-testid="pricing-tier-indicator"'),
            4,
        )
        for tier_name in ("Free", "Basic", "Main", "Premium"):
            self.assertIn(f'aria-label="Show {tier_name} tier"', body)
