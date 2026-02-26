"""Tests for the dark/light theme toggle feature (issue #124)."""

import json

from django.test import TestCase
from django.urls import reverse

from accounts.models import User


class ThemePreferenceModelTest(TestCase):
    """Tests for the theme_preference field on the User model."""

    def test_field_exists_and_defaults_empty(self):
        """User.theme_preference defaults to empty string."""
        user = User.objects.create_user(email="theme@example.com")
        self.assertEqual(user.theme_preference, "")

    def test_can_set_dark(self):
        """theme_preference can be set to 'dark'."""
        user = User.objects.create_user(email="dark@example.com")
        user.theme_preference = "dark"
        user.save(update_fields=["theme_preference"])
        user.refresh_from_db()
        self.assertEqual(user.theme_preference, "dark")

    def test_can_set_light(self):
        """theme_preference can be set to 'light'."""
        user = User.objects.create_user(email="light@example.com")
        user.theme_preference = "light"
        user.save(update_fields=["theme_preference"])
        user.refresh_from_db()
        self.assertEqual(user.theme_preference, "light")

    def test_can_set_empty(self):
        """theme_preference can be set to '' (follow system)."""
        user = User.objects.create_user(email="system@example.com")
        user.theme_preference = "dark"
        user.save(update_fields=["theme_preference"])
        user.theme_preference = ""
        user.save(update_fields=["theme_preference"])
        user.refresh_from_db()
        self.assertEqual(user.theme_preference, "")

    def test_max_length(self):
        """theme_preference has max_length=10."""
        field = User._meta.get_field("theme_preference")
        self.assertEqual(field.max_length, 10)

    def test_blank_is_true(self):
        """theme_preference field allows blank."""
        field = User._meta.get_field("theme_preference")
        self.assertTrue(field.blank)


class ThemePreferenceAPITest(TestCase):
    """Tests for POST /api/account/theme-preference endpoint."""

    def setUp(self):
        self.user = User.objects.create_user(email="api-theme@example.com")
        self.url = "/api/account/theme-preference"

    def test_url_name_resolves(self):
        """URL name api_theme_preference resolves to the correct path."""
        url = reverse("api_theme_preference")
        self.assertEqual(url, "/api/account/theme-preference")

    def test_anonymous_user_gets_redirect(self):
        """Anonymous users are redirected (login_required)."""
        response = self.client.post(
            self.url,
            data=json.dumps({"theme": "dark"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response.url)

    def test_get_not_allowed(self):
        """GET requests return 405 Method Not Allowed."""
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 405)

    def test_set_dark(self):
        """POST with theme='dark' saves preference and returns ok."""
        self.client.force_login(self.user)
        response = self.client.post(
            self.url,
            data=json.dumps({"theme": "dark"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "ok")
        self.user.refresh_from_db()
        self.assertEqual(self.user.theme_preference, "dark")

    def test_set_light(self):
        """POST with theme='light' saves preference and returns ok."""
        self.client.force_login(self.user)
        response = self.client.post(
            self.url,
            data=json.dumps({"theme": "light"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "ok")
        self.user.refresh_from_db()
        self.assertEqual(self.user.theme_preference, "light")

    def test_set_empty(self):
        """POST with theme='' clears preference and returns ok."""
        self.client.force_login(self.user)
        self.user.theme_preference = "dark"
        self.user.save(update_fields=["theme_preference"])

        response = self.client.post(
            self.url,
            data=json.dumps({"theme": ""}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "ok")
        self.user.refresh_from_db()
        self.assertEqual(self.user.theme_preference, "")

    def test_invalid_theme_value_returns_400(self):
        """POST with invalid theme value returns 400."""
        self.client.force_login(self.user)
        response = self.client.post(
            self.url,
            data=json.dumps({"theme": "blue"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertIn("error", data)

    def test_missing_theme_field_returns_400(self):
        """POST without theme field returns 400."""
        self.client.force_login(self.user)
        response = self.client.post(
            self.url,
            data=json.dumps({"other": "value"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertIn("error", data)

    def test_invalid_json_returns_400(self):
        """POST with invalid JSON returns 400."""
        self.client.force_login(self.user)
        response = self.client.post(
            self.url,
            data="not json",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertIn("error", data)

    def test_persists_across_requests(self):
        """Theme preference persists in database across requests."""
        self.client.force_login(self.user)
        self.client.post(
            self.url,
            data=json.dumps({"theme": "light"}),
            content_type="application/json",
        )
        # Create a fresh query
        user = User.objects.get(pk=self.user.pk)
        self.assertEqual(user.theme_preference, "light")


class LoginAPIThemeSyncTest(TestCase):
    """Tests for theme preference sync on login."""

    def test_login_returns_theme_preference_when_set(self):
        """Login API response includes theme_preference if user has one."""
        user = User.objects.create_user(
            email="login-theme@example.com", password="testpass123"
        )
        user.theme_preference = "light"
        user.save(update_fields=["theme_preference"])

        response = self.client.post(
            "/api/login",
            data=json.dumps({"email": "login-theme@example.com", "password": "testpass123"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["theme_preference"], "light")

    def test_login_returns_dark_theme_preference(self):
        """Login API includes theme_preference='dark' when set."""
        user = User.objects.create_user(
            email="login-dark@example.com", password="testpass123"
        )
        user.theme_preference = "dark"
        user.save(update_fields=["theme_preference"])

        response = self.client.post(
            "/api/login",
            data=json.dumps({"email": "login-dark@example.com", "password": "testpass123"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["theme_preference"], "dark")

    def test_login_omits_theme_preference_when_empty(self):
        """Login API response does not include theme_preference when empty."""
        User.objects.create_user(
            email="login-notheme@example.com", password="testpass123"
        )

        response = self.client.post(
            "/api/login",
            data=json.dumps({"email": "login-notheme@example.com", "password": "testpass123"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "ok")
        self.assertNotIn("theme_preference", data)


class ThemeToggleTemplateTest(TestCase):
    """Tests for theme toggle button presence in templates."""

    def test_homepage_has_theme_toggle(self):
        """Homepage has a theme toggle button with correct data-testid."""
        response = self.client.get("/")
        content = response.content.decode()
        self.assertIn('data-testid="theme-toggle"', content)

    def test_homepage_has_desktop_theme_toggle(self):
        """Desktop header area contains theme toggle."""
        response = self.client.get("/")
        content = response.content.decode()
        self.assertIn('data-testid="theme-toggle"', content)

    def test_homepage_has_mobile_theme_toggle(self):
        """Mobile menu area contains theme toggle."""
        response = self.client.get("/")
        content = response.content.decode()
        # There should be at least 2 toggle buttons (desktop + mobile)
        count = content.count('data-testid="theme-toggle"')
        self.assertGreaterEqual(count, 2)

    def test_toggle_has_aria_label(self):
        """Theme toggle has an aria-label for accessibility."""
        response = self.client.get("/")
        content = response.content.decode()
        self.assertIn('aria-label="Switch to light mode"', content)

    def test_toggle_has_sun_icon(self):
        """Theme toggle includes the sun icon (for dark mode)."""
        response = self.client.get("/")
        content = response.content.decode()
        self.assertIn('theme-icon-sun', content)

    def test_toggle_has_moon_icon(self):
        """Theme toggle includes the moon icon (for light mode)."""
        response = self.client.get("/")
        content = response.content.decode()
        self.assertIn('theme-icon-moon', content)


class ThemeBlockingScriptTest(TestCase):
    """Tests for the blocking theme script that prevents FOWT."""

    def test_blocking_script_in_head(self):
        """base.html includes the blocking theme script in <head>."""
        response = self.client.get("/")
        content = response.content.decode()
        # The blocking script must appear before </head>
        head_end = content.find("</head>")
        theme_script = content.find("localStorage.getItem('theme')")
        self.assertNotEqual(theme_script, -1, "Blocking theme script not found")
        self.assertLess(theme_script, head_end, "Theme script must be in <head>")

    def test_blocking_script_reads_localstorage(self):
        """Blocking script reads from localStorage."""
        response = self.client.get("/")
        content = response.content.decode()
        self.assertIn("localStorage.getItem('theme')", content)

    def test_blocking_script_checks_prefers_color_scheme(self):
        """Blocking script checks prefers-color-scheme media query."""
        response = self.client.get("/")
        content = response.content.decode()
        self.assertIn("prefers-color-scheme: dark", content)

    def test_blocking_script_manipulates_dark_class(self):
        """Blocking script adds/removes 'dark' class on html."""
        response = self.client.get("/")
        content = response.content.decode()
        self.assertIn("document.documentElement.classList.add('dark')", content)
        self.assertIn("document.documentElement.classList.remove('dark')", content)

    def test_html_tag_has_no_hardcoded_class(self):
        """The <html> tag does not have a hardcoded class attribute."""
        response = self.client.get("/")
        content = response.content.decode()
        # The html tag should be <html lang="en"> without class
        self.assertIn('<html lang="en">', content)


class ThemeCSSVariablesTest(TestCase):
    """Tests for CSS custom properties in base.html."""

    def test_light_theme_variables_defined(self):
        """Light theme CSS variables are defined in :root."""
        response = self.client.get("/")
        content = response.content.decode()
        self.assertIn("--background: 0 0% 100%", content)
        self.assertIn("--foreground: 0 0% 9%", content)

    def test_dark_theme_variables_defined(self):
        """Dark theme CSS variables are defined under .dark."""
        response = self.client.get("/")
        content = response.content.decode()
        self.assertIn("--background: 0 0% 4%", content)
        self.assertIn("--foreground: 0 0% 98%", content)

    def test_tailwind_uses_css_variables(self):
        """Tailwind config references CSS custom properties."""
        response = self.client.get("/")
        content = response.content.decode()
        self.assertIn("hsl(var(--background))", content)
        self.assertIn("hsl(var(--foreground))", content)
        self.assertIn("hsl(var(--accent))", content)

    def test_tailwind_darkmode_class(self):
        """Tailwind config has darkMode: 'class'."""
        response = self.client.get("/")
        content = response.content.decode()
        self.assertIn("darkMode", content)

    def test_body_uses_css_variables(self):
        """Body styles use CSS custom properties."""
        response = self.client.get("/")
        content = response.content.decode()
        self.assertIn("background-color: hsl(var(--background))", content)
        self.assertIn("color: hsl(var(--foreground))", content)


class ThemeProseStylesTest(TestCase):
    """Tests for prose styles using CSS custom properties."""

    def test_prose_paragraph_uses_variable(self):
        """Prose paragraph uses muted-foreground variable."""
        response = self.client.get("/")
        content = response.content.decode()
        self.assertIn("color: hsl(var(--muted-foreground))", content)

    def test_prose_code_uses_variable(self):
        """Prose inline code uses accent variable."""
        response = self.client.get("/")
        content = response.content.decode()
        self.assertIn("color: hsl(var(--accent))", content)

    def test_prose_strong_uses_variable(self):
        """Prose strong text uses foreground variable."""
        response = self.client.get("/")
        content = response.content.decode()
        self.assertIn("color: hsl(var(--foreground))", content)


class InlineGradientMigrationTest(TestCase):
    """Tests that inline HSL gradient styles have been replaced."""

    def test_homepage_no_inline_hsl_gradient(self):
        """Homepage hero does not use inline style with HSL gradient."""
        response = self.client.get("/")
        content = response.content.decode()
        self.assertNotIn(
            'style="background: radial-gradient(ellipse at top, hsl(0 0%',
            content,
        )
        self.assertIn("hero-gradient", content)

    def test_login_no_inline_hsl_gradient(self):
        """Login page does not use inline style with HSL gradient."""
        response = self.client.get("/accounts/login/")
        content = response.content.decode()
        self.assertNotIn(
            'style="background: radial-gradient(ellipse at top, hsl(0 0%',
            content,
        )
        self.assertIn("hero-gradient", content)

    def test_register_no_inline_hsl_gradient(self):
        """Register page does not use inline style with HSL gradient."""
        response = self.client.get("/accounts/register/")
        content = response.content.decode()
        self.assertNotIn(
            'style="background: radial-gradient(ellipse at top, hsl(0 0%',
            content,
        )
        self.assertIn("hero-gradient", content)

    def test_account_no_inline_hsl_gradient(self):
        """Account page does not use inline style with HSL gradient."""
        user = User.objects.create_user(email="gradient@example.com")
        self.client.force_login(user)
        response = self.client.get("/account/")
        content = response.content.decode()
        self.assertNotIn(
            'style="background: radial-gradient(ellipse at top, hsl(0 0%',
            content,
        )
        self.assertIn("hero-gradient", content)


class ThemeToggleFunctionalityScriptTest(TestCase):
    """Tests for the theme toggle JavaScript in base.html."""

    def test_toggle_function_exists(self):
        """The themeToggle.toggle function is defined."""
        response = self.client.get("/")
        content = response.content.decode()
        self.assertIn("window.themeToggle", content)
        self.assertIn("toggle: function()", content)

    def test_toggle_saves_to_localstorage(self):
        """Toggle function writes to localStorage."""
        response = self.client.get("/")
        content = response.content.decode()
        self.assertIn("localStorage.setItem('theme'", content)

    def test_authenticated_user_syncs_to_server(self):
        """For authenticated users, toggle calls the API."""
        user = User.objects.create_user(email="sync@example.com")
        self.client.force_login(user)
        response = self.client.get("/")
        content = response.content.decode()
        self.assertIn("/api/account/theme-preference", content)
        self.assertIn("isAuthenticated: true", content)

    def test_anonymous_user_does_not_sync(self):
        """For anonymous users, isAuthenticated is false."""
        response = self.client.get("/")
        content = response.content.decode()
        self.assertIn("isAuthenticated: false", content)

    def test_hero_gradient_css_class_defined(self):
        """The hero-gradient CSS class is defined."""
        response = self.client.get("/")
        content = response.content.decode()
        self.assertIn(".hero-gradient", content)

    def test_hero_gradient_uses_css_variables(self):
        """The hero-gradient class uses CSS custom properties."""
        response = self.client.get("/")
        content = response.content.decode()
        self.assertIn("--hero-gradient-mid", content)
        self.assertIn("--hero-gradient-end", content)
