import re

from django.contrib.auth import get_user_model
from django.test import TestCase

User = get_user_model()


class HeaderMobileMenuTest(TestCase):
    """Tests for mobile header navigation improvements (issue #172)."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email="testuser@example.com",
            password="testpass123",
        )

    def test_home_page_renders(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)

    def test_hamburger_icon_has_open_and_close_svgs(self):
        """The mobile menu button should contain both a hamburger (open) and X (close) SVG icon."""
        response = self.client.get("/")
        content = response.content.decode()
        self.assertIn('id="mobile-menu-icon-open"', content)
        self.assertIn('id="mobile-menu-icon-close"', content)

    def test_close_icon_hidden_by_default(self):
        """The X icon should be hidden by default (menu starts closed)."""
        response = self.client.get("/")
        content = response.content.decode()
        # The close icon SVG should have 'hidden' class
        close_icon_start = content.index('id="mobile-menu-icon-close"')
        svg_start = content.rfind("<svg", 0, close_icon_start)
        svg_tag = content[svg_start:close_icon_start]
        self.assertIn("hidden", svg_tag)

    def test_hamburger_button_has_44px_tap_target(self):
        """The hamburger button should have min-h-[44px] and min-w-[44px] for tap targets."""
        response = self.client.get("/")
        content = response.content.decode()
        self.assertIn('min-h-[44px]', content)
        self.assertIn('min-w-[44px]', content)

    def test_mobile_nav_links_have_py3_padding(self):
        """Mobile menu nav links should use py-3 for 44px tap target height."""
        response = self.client.get("/")
        content = response.content.decode()
        menu_start = content.index('id="mobile-menu"')
        menu_html = content[menu_start:]
        match = re.search(r'<a[^>]*href="/courses"[^>]*>', menu_html)
        self.assertIsNotNone(match, "Courses link not found in mobile menu")
        self.assertIn("py-3", match.group(0))

    def test_notification_dropdown_has_max_width_constraint(self):
        """The notification dropdown should be constrained to viewport width for authenticated users."""
        self.client.login(email="testuser@example.com", password="testpass123")
        response = self.client.get("/")
        content = response.content.decode()
        self.assertIn('max-w-[calc(100vw-2rem)]', content)

    def test_logo_text_has_truncate_class(self):
        """The logo text should truncate on narrow screens."""
        response = self.client.get("/")
        content = response.content.decode()
        logo_pos = content.index("AI Shipping Labs</span>")
        span_start = content.rfind("<span", 0, logo_pos)
        span_tag = content[span_start:logo_pos]
        self.assertIn("truncate", span_tag)

    def test_logo_link_has_min_w_0(self):
        """The logo link should have min-w-0 to allow truncation."""
        response = self.client.get("/")
        content = response.content.decode()
        self.assertIn('min-w-0', content)

    def test_mobile_nav_sections_have_chevron_indicators(self):
        """The Learn and Community sections should have chevron SVG indicators."""
        response = self.client.get("/")
        content = response.content.decode()
        self.assertIn('id="mobile-learn-chevron"', content)
        self.assertIn('id="mobile-learn-toggle"', content)
        self.assertIn('id="mobile-community-chevron"', content)
        self.assertIn('id="mobile-community-toggle"', content)

    def test_mobile_nav_toggles_are_buttons(self):
        """The Learn and Community headings should be buttons."""
        response = self.client.get("/")
        content = response.content.decode()
        for toggle_id in ['mobile-learn-toggle', 'mobile-community-toggle']:
            toggle_pos = content.index(f'id="{toggle_id}"')
            tag_start = content.rfind("<", 0, toggle_pos)
            tag_name = content[tag_start:tag_start + 10]
            self.assertTrue(tag_name.startswith("<button"))

    def test_learn_and_community_links_are_grouped(self):
        """Learn and Community expose the requested grouped links."""
        response = self.client.get("/")
        content = response.content.decode()
        self.assertIn('id="learn-dropdown-btn"', content)
        self.assertIn('id="community-dropdown-btn"', content)
        self.assertIn('href="/courses"', content)
        self.assertIn('href="/workshops"', content)
        self.assertIn('href="/learning-path/ai-engineer"', content)
        self.assertIn('href="/projects"', content)
        self.assertIn('href="/interview"', content)
        self.assertIn('href="/blog"', content)
        self.assertIn('href="/sprints"', content)
        self.assertIn('href="/events"', content)
        self.assertIn('href="/activities"', content)
        self.assertIn('href="/resources"', content)
        self.assertNotIn('id="resources-dropdown-btn"', content)

    def test_close_on_outside_click_script_present(self):
        """The script should include close-on-outside-click logic for the mobile menu."""
        response = self.client.get("/")
        content = response.content.decode()
        self.assertIn("Close mobile menu when clicking outside", content)

    def test_icon_toggle_script_present(self):
        """The script should toggle between hamburger and X icons."""
        response = self.client.get("/")
        content = response.content.decode()
        self.assertIn("mobile-menu-icon-open", content)
        self.assertIn("mobile-menu-icon-close", content)
        self.assertIn("closeMenu", content)
        self.assertIn("openMenu", content)
