"""Tests for the dark/light theme toggle feature (issue #124)."""

import json

from django.test import TestCase

from accounts.models import User


class ThemePreferenceAPITest(TestCase):
    """Tests for POST /api/account/theme-preference endpoint."""

    def setUp(self):
        self.user = User.objects.create_user(email="api-theme@example.com")
        self.url = "/api/account/theme-preference"

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


# `ThemeToggleTemplateTest` removed under `_docs/testing-guidelines.md` Rule 4
# (template string-matching: `data-testid="theme-toggle"`, `theme-icon-sun`,
# `theme-icon-moon`). Toggle behavior (clicks change theme, persists across nav)
# belongs in Playwright -- see follow-up issue #267.
