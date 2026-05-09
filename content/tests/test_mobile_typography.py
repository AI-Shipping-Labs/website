import re

from django.contrib.auth import get_user_model
from django.test import TestCase

User = get_user_model()


class MobileTypographyAndSpacingTest(TestCase):
    """Tests for mobile typography, spacing, and touch targets (issue #180)."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email="testuser@example.com",
            password="testpass123",
        )

    # ── iOS auto-zoom prevention ──

    def test_base_html_has_mobile_input_font_size_rule(self):
        """base.html should include a CSS rule setting inputs to 16px on mobile to prevent iOS auto-zoom."""
        response = self.client.get("/")
        content = response.content.decode()
        self.assertIn("font-size: 16px", content)
        self.assertIn("input, textarea, select", content)

    def test_login_page_renders(self):
        """Login page should render without errors."""
        response = self.client.get("/accounts/login/")
        self.assertEqual(response.status_code, 200)

    def test_register_page_renders(self):
        """Register page should render without errors."""
        response = self.client.get("/accounts/register/")
        self.assertEqual(response.status_code, 200)

    # ── Prose overflow-wrap ──

    def test_prose_has_overflow_wrap(self):
        """The .prose CSS should include overflow-wrap or word-break for long URLs."""
        response = self.client.get("/")
        content = response.content.decode()
        # Check that .prose style includes word-break: break-word
        prose_section = content[content.index(".prose {"):]
        prose_rule_end = prose_section.index("}")
        prose_rule = prose_section[:prose_rule_end]
        self.assertIn("break-word", prose_rule)

    # ── Theme toggle touch targets ──

    def test_desktop_theme_toggle_has_adequate_padding(self):
        """Desktop theme toggle button should have p-2.5 for a 44px touch target."""
        response = self.client.get("/")
        content = response.content.decode()
        # Find the desktop theme toggle (first one with data-testid="theme-toggle")
        toggle_match = re.search(
            r'<button[^>]*data-testid="theme-toggle"[^>]*>', content
        )
        self.assertIsNotNone(toggle_match)
        self.assertIn("p-2.5", toggle_match.group(0))

    def test_mobile_theme_toggle_has_adequate_padding(self):
        """Mobile theme toggle button should also have p-2.5 for a 44px touch target."""
        response = self.client.get("/")
        content = response.content.decode()
        # Find all theme toggles - the second one is mobile
        toggles = re.findall(
            r'<button[^>]*data-testid="theme-toggle"[^>]*>', content
        )
        self.assertGreaterEqual(len(toggles), 2, "Expected at least 2 theme toggles")
        self.assertIn("p-2.5", toggles[1])

    # ── Notification bell touch target ──

    def test_notification_bell_has_adequate_padding(self):
        """Notification bell button should have p-2.5 for a 44px touch target."""
        self.client.login(email="testuser@example.com", password="testpass123")
        response = self.client.get("/")
        content = response.content.decode()
        bell_match = re.search(
            r'<button[^>]*id="notification-bell-btn"[^>]*>', content
        )
        self.assertIsNotNone(bell_match)
        self.assertIn("p-2.5", bell_match.group(0))

    # ── Toggle switch touch targets ──

    def test_billing_toggle_has_touch_target_wrapper(self):
        """The billing toggle on the home page should be wrapped with a touch-target-toggle class."""
        response = self.client.get("/")
        content = response.content.decode()
        # Find the billing toggle and check it is inside a touch-target wrapper
        toggle_pos = content.index('id="billing-toggle"')
        # Look backwards for the wrapper div
        preceding = content[max(0, toggle_pos - 200):toggle_pos]
        self.assertIn("touch-target-toggle", preceding)

    def test_newsletter_toggle_has_touch_target_wrapper(self):
        """The newsletter toggle on the account page should be wrapped with a touch-target-toggle class."""
        self.client.login(email="testuser@example.com", password="testpass123")
        response = self.client.get("/account/")
        content = response.content.decode()
        toggle_pos = content.index('id="newsletter-toggle"')
        preceding = content[max(0, toggle_pos - 200):toggle_pos]
        self.assertIn("touch-target-toggle", preceding)

    def test_touch_target_toggle_css_defined(self):
        """The touch-target-toggle CSS class should be defined with min-height: 44px."""
        response = self.client.get("/")
        content = response.content.decode()
        self.assertIn("touch-target-toggle", content)
        self.assertIn("min-height: 44px", content)

    # ── Section vertical padding reduced on mobile ──

    def test_home_sections_use_reduced_mobile_padding(self):
        """Home page sections should use the tightened mobile spacing pattern."""
        response = self.client.get("/")
        content = response.content.decode()
        # The about section should have the responsive padding pattern
        about_match = re.search(r'id="about"[^>]*class="[^"]*"', content)
        self.assertIsNotNone(about_match)
        self.assertIn("py-12", about_match.group(0))
        self.assertIn("sm:py-20", about_match.group(0))
        self.assertIn("lg:py-28", about_match.group(0))

    def test_hero_uses_reduced_mobile_padding(self):
        """Hero section inner div should use the tightened mobile spacing pattern."""
        response = self.client.get("/")
        content = response.content.decode()
        self.assertIn("px-4 py-10 sm:px-6 sm:py-20 lg:px-8 lg:py-28", content)

    def test_login_uses_reduced_mobile_padding(self):
        """Login page should use reduced padding on mobile."""
        response = self.client.get("/accounts/login/")
        content = response.content.decode()
        self.assertIn("px-4 py-8 sm:px-6 sm:py-10 lg:px-8 lg:py-12", content)

    def test_footer_uses_reduced_mobile_padding(self):
        """Footer should use reduced padding on mobile."""
        response = self.client.get("/")
        content = response.content.decode()
        self.assertIn("py-10 sm:py-16", content)

    # ── Prose tables still scrollable ──

    def test_prose_tables_still_have_overflow_x_auto(self):
        """Prose table styles should retain overflow-x: auto for horizontal scrolling."""
        response = self.client.get("/")
        content = response.content.decode()
        # Find .prose table rule
        table_pos = content.index(".prose table")
        table_rule = content[table_pos:content.index("}", table_pos)]
        self.assertIn("overflow-x: auto", table_rule)

    # ── Content offset still correct ──

    def test_pt_24_content_offset_present(self):
        """Pages with fixed header should still have pt-24 content offset."""
        response = self.client.get("/")
        content = response.content.decode()
        self.assertIn("pt-24", content)

    # ── Both themes work ──

    def test_light_and_dark_theme_css_vars_present(self):
        """Both light and dark theme CSS variables should be present in the base template."""
        response = self.client.get("/")
        content = response.content.decode()
        self.assertIn(":root {", content)
        self.assertIn(".dark {", content)
