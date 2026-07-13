"""Homepage membership-card layout regression coverage for issue #1235."""

from pathlib import Path

from django.test import SimpleTestCase

HOME_TEMPLATE = Path(__file__).parents[2] / 'templates' / 'home.html'
PRICING_TEMPLATE = (
    Path(__file__).parents[2] / 'templates' / 'payments' / 'pricing.html'
)


def _tier_grid_markup(template):
    marker = 'data-testid="home-tier-carousel"'
    marker_index = template.index(marker)
    opening_tag_start = template.rfind('<div ', 0, marker_index)
    return template[opening_tag_start:marker_index]


def _tier_section_markup(template):
    start = template.index('<section id="tiers"')
    end = template.index('<section ', start + 1)
    return template[start:end]


class HomepageTierLayoutTemplateTest(SimpleTestCase):
    def setUp(self):
        self.home = HOME_TEMPLATE.read_text(encoding='utf-8')
        self.tier_section = _tier_section_markup(self.home)

    def test_desktop_grid_starts_cards_at_their_intrinsic_height(self):
        grid = _tier_grid_markup(self.home)

        self.assertIn('lg:grid-cols-4', grid)
        self.assertIn('lg:items-start', grid)
        self.assertNotIn('items-stretch', grid)
        self.assertNotIn('content-stretch', grid)

    def test_no_homepage_tier_card_forces_full_desktop_height(self):
        self.assertEqual(self.tier_section.count('data-testid="home-tier-card"'), 2)
        self.assertEqual(self.tier_section.count('lg:min-h-full'), 0)
        self.assertEqual(self.tier_section.count('min-h-screen'), 0)

    def test_free_and_paid_cta_contracts_remain_separate(self):
        self.assertEqual(
            self.tier_section.count('data-testid="home-free-tier-cta"'), 1
        )
        self.assertIn('href="/accounts/register/"', self.tier_section)
        self.assertIn('Join free', self.tier_section)
        self.assertNotIn('inline-register-card', self.tier_section)
        self.assertNotIn('<form', self.tier_section)
        self.assertIn('data-link-annual=', self.tier_section)
        self.assertIn('data-link-monthly=', self.tier_section)
        self.assertIn('Most Popular', self.tier_section)

    def test_pricing_reference_keeps_its_existing_start_aligned_grid(self):
        pricing = PRICING_TEMPLATE.read_text(encoding='utf-8')
        marker = 'data-testid="pricing-tier-carousel"'
        marker_index = pricing.index(marker)
        opening_tag_start = pricing.rfind('<div ', 0, marker_index)
        grid = pricing[opening_tag_start:marker_index]

        self.assertIn('items-start', grid)
        self.assertIn('lg:grid-cols-4', grid)
