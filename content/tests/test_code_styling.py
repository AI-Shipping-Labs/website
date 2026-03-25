"""Tests for unified code block styling - issue #146.

Verifies that the CSS in base.html meets the code styling requirements:
- Inline code uses neutral text color (not accent/green)
- Inline code has a border
- pre code inside blocks resets border and background
- Codehilite blocks use CSS variable backgrounds (not hardcoded Monokai)
- font-variant-ligatures: none is preserved on all code elements
- No hardcoded dark override for pre code color
"""

from django.test import TestCase


class CodeStylingCSSTest(TestCase):
    """Verify code styling CSS rules are present in the rendered base template.

    Uses the homepage (/) as a vehicle to check CSS in base.html.
    """

    @classmethod
    def setUpTestData(cls):
        # Fetch homepage once -- base.html CSS is included on every page
        cls.response_content = None

    def _get_page_content(self):
        if self.__class__.response_content is None:
            response = self.client.get("/")
            self.__class__.response_content = response.content.decode()
        return self.__class__.response_content

    # -- Inline code (.prose code) --

    def test_inline_code_uses_foreground_color_not_accent(self):
        """Inline code should use --foreground for text, not --accent (green)."""
        html = self._get_page_content()
        # The .prose code rule should reference --foreground
        self.assertIn("color: hsl(var(--foreground))", html)
        # Should NOT use --accent for inline code color
        # (accent is still used for links, so we check specifically in the
        # .prose code rule context by verifying the old pattern is gone)
        self.assertNotIn(
            "color: hsl(var(--accent)); font-variant-ligatures",
            html,
        )

    def test_inline_code_has_border(self):
        """Inline code should have a subtle border for visual consistency."""
        html = self._get_page_content()
        self.assertIn(
            "border: 1px solid hsl(var(--border)); font-variant-ligatures: none",
            html,
        )

    # -- pre code reset --

    def test_pre_code_resets_border(self):
        """Code inside pre blocks should not inherit the inline code border."""
        html = self._get_page_content()
        self.assertIn(".prose pre code", html)
        self.assertIn(
            "border: none; color: hsl(var(--card-foreground))",
            html,
        )

    def test_no_hardcoded_dark_pre_code_override(self):
        """The old .dark .prose pre code hardcoded color override should be removed."""
        html = self._get_page_content()
        self.assertNotIn(".dark .prose pre code", html)

    # -- Codehilite blocks --

    def test_codehilite_uses_css_variable_background(self):
        """Codehilite blocks should use --card variable, not hardcoded #272822."""
        html = self._get_page_content()
        self.assertNotIn("#272822", html)
        self.assertIn(
            ".prose .codehilite { background: hsl(var(--card))",
            html,
        )

    def test_codehilite_light_theme_uses_css_variable(self):
        """Light codehilite should not have a separate hardcoded background.

        Since .codehilite now uses hsl(var(--card)) which adapts to theme,
        the light override only needs to set the text color.
        """
        html = self._get_page_content()
        # Old hardcoded light bg should be gone
        self.assertNotIn("hsl(0 0% 97%)", html)

    def test_codehilite_code_resets_border(self):
        """Code inside codehilite should not have the inline code border."""
        html = self._get_page_content()
        self.assertIn(
            ".prose .codehilite code { background: transparent; padding: 0; border: none",
            html,
        )

    # -- Ligatures --

    def test_font_variant_ligatures_preserved_on_inline_code(self):
        """font-variant-ligatures: none must be on .prose code."""
        html = self._get_page_content()
        # Check the inline code rule has ligatures disabled
        self.assertIn(
            ".prose code { border-radius: 0.25rem",
            html,
        )
        # Find the .prose code rule and verify it contains font-variant-ligatures
        import re
        match = re.search(r"\.prose code \{[^}]+\}", html)
        self.assertIsNotNone(match, "Could not find .prose code rule in CSS")
        self.assertIn("font-variant-ligatures: none", match.group())

    def test_font_variant_ligatures_preserved_on_pre(self):
        """font-variant-ligatures: none must be on .prose pre."""
        html = self._get_page_content()
        import re
        match = re.search(r"\.prose pre \{[^}]+\}", html)
        self.assertIsNotNone(match, "Could not find .prose pre rule in CSS")
        self.assertIn("font-variant-ligatures: none", match.group())

    def test_font_variant_ligatures_preserved_on_codehilite(self):
        """font-variant-ligatures: none must be on .prose .codehilite."""
        html = self._get_page_content()
        import re
        match = re.search(r"\.prose \.codehilite \{[^}]+\}", html)
        self.assertIsNotNone(match, "Could not find .prose .codehilite rule in CSS")
        self.assertIn("font-variant-ligatures: none", match.group())

    # -- Contrast (dark theme error token) --

    def test_dark_error_token_not_on_dark_background(self):
        """The dark theme error token should not have a dark background color."""
        html = self._get_page_content()
        self.assertNotIn("#1E0010", html)

    def test_dark_error_token_uses_accessible_color(self):
        """The error token color should be #FF3399 for WCAG AA contrast."""
        html = self._get_page_content()
        self.assertIn(".codehilite .err { color: #FF3399", html)
