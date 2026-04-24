import re

from django.test import TestCase


class HomepageMobileLayoutTest(TestCase):
    """Tests for homepage mobile layout fixes (issue #173)."""

    def _get_homepage_content(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    # -- Hero stats grid responsive --

    def _find_hero_stats_grid(self, content):
        """Find the grid div containing Build/Ship/Grow stats."""
        match = re.search(r'<div[^>]*class="[^"]*\bgrid\b[^"]*"[^>]*>\s*<div>\s*<p[^>]*>Build</p>', content)
        self.assertIsNotNone(match, "Hero stats grid not found")
        return match.group(0)

    def test_hero_stats_grid_stacks_on_mobile(self):
        """Hero stats grid should use grid-cols-1 on mobile and grid-cols-3 on sm+ screens."""
        content = self._get_homepage_content()
        grid_tag = self._find_hero_stats_grid(content)
        self.assertIn("grid-cols-1", grid_tag)
        self.assertIn("sm:grid-cols-3", grid_tag)

    def test_hero_stats_grid_has_reduced_mobile_gap(self):
        """Hero stats grid should use gap-4 on mobile (not gap-8)."""
        content = self._get_homepage_content()
        grid_tag = self._find_hero_stats_grid(content)
        self.assertIn("gap-4", grid_tag)
        self.assertIn("sm:gap-8", grid_tag)

    # -- Billing toggle touch target --

    def test_billing_toggle_has_touch_target_wrapper(self):
        """The billing toggle should be wrapped with touch-target-toggle for 44px minimum tap area."""
        content = self._get_homepage_content()
        toggle_pos = content.index('id="billing-toggle"')
        preceding = content[max(0, toggle_pos - 200):toggle_pos]
        self.assertIn("touch-target-toggle", preceding)

    # -- View all links have adequate tap targets --

    def test_view_all_recordings_link_has_tap_target(self):
        """View all recordings link should have py-2 and px-3 for adequate mobile tap target."""
        content = self._get_homepage_content()
        match = re.search(r'<a[^>]*href="/events\?filter=past"[^>]*>', content)
        self.assertIsNotNone(match, "View all recordings link not found")
        self.assertIn("py-2", match.group(0))
        self.assertIn("px-3", match.group(0))

    def test_view_all_posts_link_has_tap_target(self):
        """View all posts link should have py-2 and px-3 for adequate mobile tap target."""
        content = self._get_homepage_content()
        match = re.search(r'<a[^>]*href="/blog"[^>]*>', content)
        self.assertIsNotNone(match, "View all posts link not found")
        self.assertIn("py-2", match.group(0))
        self.assertIn("px-3", match.group(0))

    def test_view_all_projects_link_has_tap_target(self):
        """View all projects link should have py-2 and px-3 for adequate mobile tap target."""
        content = self._get_homepage_content()
        match = re.search(r'<a[^>]*href="/projects"[^>]*>', content)
        self.assertIsNotNone(match, "View all projects link not found")
        self.assertIn("py-2", match.group(0))
        self.assertIn("px-3", match.group(0))

    def test_view_all_curated_links_has_tap_target(self):
        """View all curated links should have py-2 and px-3 for adequate mobile tap target."""
        content = self._get_homepage_content()
        match = re.search(r'<a[^>]*href="/collection"[^>]*>', content)
        self.assertIsNotNone(match, "View all curated links link not found")
        self.assertIn("py-2", match.group(0))
        self.assertIn("px-3", match.group(0))

    # -- Pricing section overflow --

    def test_pricing_section_has_overflow_hidden(self):
        """Pricing section should have overflow-x-hidden to prevent horizontal scroll on mobile."""
        content = self._get_homepage_content()
        tiers_match = re.search(r'id="tiers"[^>]*class="([^"]*)"', content)
        self.assertIsNotNone(tiers_match, "Tiers section not found")
        self.assertIn("overflow-x-hidden", tiers_match.group(1))

    def test_pricing_cards_stack_on_mobile(self):
        """Pricing cards should use lg:grid-cols-3 (stacking on mobile by default)."""
        content = self._get_homepage_content()
        # Find the pricing grid
        tiers_pos = content.index('id="tiers"')
        grid_match = re.search(r'class="[^"]*lg:grid-cols-3[^"]*"', content[tiers_pos:])
        self.assertIsNotNone(grid_match, "Pricing grid with lg:grid-cols-3 not found")

    def test_highlighted_card_scale_only_on_large_screens(self):
        """The highlighted pricing card should only scale on lg+ screens (lg:scale-105)."""
        content = self._get_homepage_content()
        # Find scale-105 and verify it has lg: prefix
        if "scale-105" in content:
            self.assertIn("lg:scale-105", content)
            # Ensure there is no bare scale-105 without the lg: prefix
            bare_scale = re.findall(r'(?<!lg:)scale-105', content)
            self.assertEqual(len(bare_scale), 0, "Found scale-105 without lg: prefix")

    # -- Theme compatibility --

    def test_both_themes_css_vars_present(self):
        """Both light and dark theme CSS variables should be present."""
        content = self._get_homepage_content()
        self.assertIn(":root {", content)
        self.assertIn(".dark {", content)

    # -- Hero CTA buttons --

    def test_hero_cta_buttons_full_width_on_mobile(self):
        """Hero CTA buttons should be full-width on mobile (w-full) and auto-width on sm+ (sm:w-auto)."""
        content = self._get_homepage_content()
        subscribe_match = re.search(r'<a[^>]*href="/#newsletter"[^>]*>', content)
        self.assertIsNotNone(subscribe_match, "Subscribe CTA link not found")
        self.assertIn("w-full", subscribe_match.group(0))
        self.assertIn("sm:w-auto", subscribe_match.group(0))

    # -- No bare grid-cols-3 without responsive in hero stats --

    def test_no_bare_grid_cols_3_in_hero_stats(self):
        """The hero stats section should not have bare grid-cols-3 without a mobile-first breakpoint."""
        content = self._get_homepage_content()
        grid_tag = self._find_hero_stats_grid(content)
        # Should not have grid-cols-3 without sm: prefix in this context
        # grid-cols-1 should come first
        cols1_pos = grid_tag.index("grid-cols-1")
        cols3_pos = grid_tag.index("sm:grid-cols-3")
        self.assertLess(cols1_pos, cols3_pos, "grid-cols-1 should come before sm:grid-cols-3")
