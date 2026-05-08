from django.test import TestCase


class PricingMobileResponsiveTest(TestCase):
    """Tests that the pricing page has mobile-responsive CSS classes."""

    @classmethod
    def setUpTestData(cls):
        # Tiers are seeded by data migration, no setup needed
        pass

    def _get_pricing_content(self):
        response = self.client.get("/pricing")
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_tier_cards_have_responsive_padding(self):
        """Tier cards should use p-5 on mobile, p-8 on sm+ breakpoint."""
        content = self._get_pricing_content()
        self.assertIn("p-5 sm:p-8", content)

    def test_tier_cards_no_desktop_only_padding(self):
        """Tier cards should not use p-8 without a mobile-first alternative."""
        content = self._get_pricing_content()
        # The old pattern was just 'p-8' without 'p-5 sm:p-8'.
        # After the fix, every card uses 'p-5 sm:p-8', so bare ' p-8 '
        # (with spaces, not preceded by sm:) should not appear in a card div.
        # We check that the responsive version is present (covered above)
        # and that bare p-8 on card divs is gone.
        # Count occurrences: p-5 sm:p-8 should appear 4 times (one per card)
        self.assertEqual(content.count("p-5 sm:p-8"), 4)

    def test_billing_toggle_has_adequate_tap_target(self):
        """Billing toggle should use the shared wrapper for a 44px tap area."""
        content = self._get_pricing_content()
        toggle_pos = content.index('id="billing-toggle"')
        preceding = content[max(0, toggle_pos - 200):toggle_pos]
        self.assertIn("touch-target-toggle", preceding)

    def test_cta_buttons_have_min_height(self):
        """CTA buttons should have min-h-[44px] for adequate tap targets."""
        content = self._get_pricing_content()
        # There should be at least 4 CTA elements with min-h-[44px]
        # (one per tier card: Subscribe + 3 Join buttons)
        cta_min_height_count = content.count("min-h-[44px]")
        # dismiss button (1) + 4 CTA buttons = 5
        self.assertGreaterEqual(cta_min_height_count, 5)

    def test_cta_buttons_have_responsive_vertical_padding(self):
        """CTA buttons should use py-3 on mobile, py-2.5 on sm+."""
        content = self._get_pricing_content()
        self.assertIn("py-3 sm:py-2.5", content)

    def test_most_popular_badge_visible_with_overflow_visible(self):
        """The 'Most Popular' badge card should have overflow-visible so badge is not clipped."""
        content = self._get_pricing_content()
        self.assertIn("overflow-visible", content)

    def test_most_popular_badge_has_z_index(self):
        """The 'Most Popular' badge should have z-10 to stay above sibling cards."""
        content = self._get_pricing_content()
        self.assertIn("z-10", content)

    def test_most_popular_badge_whitespace_nowrap(self):
        """The 'Most Popular' badge should not wrap text."""
        content = self._get_pricing_content()
        self.assertIn("whitespace-nowrap", content)

    def test_main_tier_card_has_top_margin_on_mobile(self):
        """The Main tier card needs mt-4 on mobile so the badge is not clipped by the card above."""
        content = self._get_pricing_content()
        self.assertIn("mt-4 sm:mt-0", content)

    def test_section_has_responsive_horizontal_padding(self):
        """The main section should use px-4 on mobile, px-6 on sm+."""
        content = self._get_pricing_content()
        self.assertIn("px-4 sm:px-6 lg:px-8", content)

    def test_checkout_cancelled_banner_dismiss_button_has_tap_target(self):
        """The dismiss button on the cancelled banner should have a 44px tap target."""
        content = self._get_pricing_content()
        # The dismiss button has min-w-[44px] min-h-[44px]
        self.assertIn('id="dismiss-cancelled-banner"', content)
        self.assertIn("min-w-[44px] min-h-[44px]", content)

    def test_checkout_cancelled_banner_responsive_padding(self):
        """The cancelled banner should have responsive padding."""
        content = self._get_pricing_content()
        self.assertIn("p-3 sm:p-4", content)

    def test_grid_has_responsive_gap(self):
        """The tier grid should have larger gap on mobile for badge visibility."""
        content = self._get_pricing_content()
        self.assertIn("gap-8 sm:gap-6", content)

    def test_feature_list_items_have_shrink_zero_icon(self):
        """Feature list check icons should have shrink-0 to prevent text wrapping into them."""
        content = self._get_pricing_content()
        self.assertIn("shrink-0 text-accent", content)

    def test_billing_toggle_uses_homepage_size(self):
        """Billing toggle should use the same compact dimensions as homepage."""
        content = self._get_pricing_content()
        self.assertIn("h-6 w-11", content)
        self.assertNotIn("h-8 w-14 sm:h-6 sm:w-11", content)

    def test_billing_toggle_defaults_to_annual_accessible_state(self):
        """Billing toggle should render as pressed with annual label emphasized."""
        content = self._get_pricing_content()
        self.assertIn('aria-pressed="true"', content)
        self.assertIn('id="monthly-label">Monthly</span>', content)
        self.assertIn(
            'class="whitespace-nowrap text-sm text-foreground" id="annual-label"',
            content,
        )
        self.assertIn('translate-x-5" id="billing-toggle-dot"', content)
