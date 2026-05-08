"""Tests for footer responsive fixes (issue #179).

Covers:
- Footer padding reduced on mobile (py-10 sm:py-16 lg:py-24)
- Newsletter form stacks vertically on mobile with adequate spacing
- Footer community links keep mobile tap targets and tighten at sm+
- Subscribe form success/error messages visible on mobile
- No horizontal overflow risk (no fixed-width elements)
- Both light and dark themes work
"""

import re

from django.test import TestCase


def _extract_footer(html):
    """Extract the <footer>...</footer> block from the HTML."""
    match = re.search(r"<footer[\s\S]*?</footer>", html)
    assert match, "No <footer> element found in response"
    return match.group(0)


class FooterResponsivePaddingTest(TestCase):
    """Footer uses responsive padding for tighter mobile spacing."""

    def test_footer_outer_div_has_responsive_padding(self):
        """Footer wrapper should use py-10 sm:py-16 lg:py-24 pattern."""
        response = self.client.get("/")
        footer = _extract_footer(response.content.decode())
        self.assertIn("py-10 sm:py-16", footer)
        self.assertIn("lg:py-24", footer)


class FooterNewsletterFormMobileTest(TestCase):
    """Newsletter subscribe form is usable on mobile viewports."""

    def test_form_uses_flex_col_for_mobile_stacking(self):
        """Form should use flex-col so input and button stack on mobile."""
        response = self.client.get("/")
        footer = _extract_footer(response.content.decode())
        form_match = re.search(
            r'<form[^>]*class="subscribe-form[^"]*"', footer
        )
        self.assertIsNotNone(form_match, "Subscribe form not found in footer")
        form_classes = form_match.group(0)
        self.assertIn("flex-col", form_classes)
        self.assertIn("gap-3", form_classes)

    def test_email_input_is_full_width_on_mobile(self):
        """Email input should have w-full class for mobile full-width."""
        response = self.client.get("/")
        footer = _extract_footer(response.content.decode())
        input_match = re.search(
            r'<input[^>]*name="email"[^>]*class="([^"]*)"', footer
        )
        self.assertIsNotNone(input_match, "Email input not found in footer")
        classes = input_match.group(1)
        self.assertIn("w-full", classes)

    def test_subscribe_success_message_element_exists(self):
        """Footer should contain the success message element for JS to populate."""
        response = self.client.get("/")
        footer = _extract_footer(response.content.decode())
        self.assertIn("footer-subscribe-message", footer)

    def test_subscribe_error_message_element_exists(self):
        """Footer should contain the error message element for JS to populate."""
        response = self.client.get("/")
        footer = _extract_footer(response.content.decode())
        self.assertIn("footer-subscribe-error", footer)


class FooterTapTargetsTest(TestCase):
    """Footer community links balance mobile tap targets with compact desktop rows."""

    def test_footer_about_link_preserves_mobile_tap_target(self):
        """The About link keeps a 44px minimum tap target below sm."""
        response = self.client.get("/")
        footer = _extract_footer(response.content.decode())
        about_match = re.search(
            r'<a[^>]*href="/about"[^>]*class="([^"]*)"', footer
        )
        self.assertIsNotNone(about_match, "About link not found in footer")
        classes = about_match.group(1)
        self.assertIn("min-h-[44px]", classes)
        self.assertIn("py-2", classes)

    def test_footer_about_link_uses_inline_flex(self):
        """About link should use inline-flex items-center for vertical centering."""
        response = self.client.get("/")
        footer = _extract_footer(response.content.decode())
        about_match = re.search(
            r'<a[^>]*href="/about"[^>]*class="([^"]*)"', footer
        )
        self.assertIsNotNone(about_match, "About link not found in footer")
        classes = about_match.group(1)
        self.assertIn("inline-flex", classes)
        self.assertIn("items-center", classes)

    def test_footer_faq_link_compacts_at_sm_and_above(self):
        """The FAQ link removes the 44px row height on wider layouts."""
        response = self.client.get("/")
        footer = _extract_footer(response.content.decode())
        faq_match = re.search(
            r'<a[^>]*href="/about#faq"[^>]*class="([^"]*)"', footer
        )
        self.assertIsNotNone(faq_match, "FAQ link not found in footer")
        classes = faq_match.group(1)
        self.assertIn("min-h-[44px]", classes)
        self.assertIn("sm:min-h-0", classes)
        self.assertIn("sm:py-1", classes)

    def test_footer_link_lists_remove_extra_row_gaps(self):
        """Community and Legal link lists should not add gaps between tap rows."""
        response = self.client.get("/")
        footer = _extract_footer(response.content.decode())
        self.assertIn('class="mt-3 space-y-0 sm:mt-4"', footer)
        self.assertNotIn('class="mt-4 space-y-1"', footer)


class FooterNoHorizontalOverflowTest(TestCase):
    """Footer should not cause horizontal overflow on narrow viewports."""

    def test_footer_email_input_has_full_width_on_mobile(self):
        """Email input should have w-full for mobile, not a fixed width."""
        response = self.client.get("/")
        footer = _extract_footer(response.content.decode())
        input_match = re.search(
            r'<input[^>]*name="email"[^>]*class="([^"]*)"', footer
        )
        self.assertIsNotNone(input_match)
        self.assertIn("w-full", input_match.group(1))

    def test_footer_container_has_max_width(self):
        """Footer content should be constrained by max-w-7xl."""
        response = self.client.get("/")
        footer = _extract_footer(response.content.decode())
        self.assertIn("max-w-7xl", footer)


class FooterThemeCompatibilityTest(TestCase):
    """Footer works in both light and dark themes."""

    def test_footer_uses_theme_aware_background(self):
        """Footer should use theme-aware bg-card class."""
        response = self.client.get("/")
        footer = _extract_footer(response.content.decode())
        footer_tag = re.search(r'<footer[^>]*class="([^"]*)"', footer)
        self.assertIsNotNone(footer_tag, "Footer tag not found")
        self.assertIn("bg-card", footer_tag.group(1))

    def test_footer_links_use_theme_foreground_colors(self):
        """Footer links should use muted-foreground for theme compatibility."""
        response = self.client.get("/")
        footer = _extract_footer(response.content.decode())
        about_match = re.search(
            r'<a[^>]*href="/about"[^>]*class="([^"]*)"', footer
        )
        self.assertIsNotNone(about_match)
        self.assertIn("text-muted-foreground", about_match.group(1))
