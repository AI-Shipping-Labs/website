"""Auth throttle IntegrationSetting keys (issue #1516)."""

import os

from django.conf import settings
from django.test import SimpleTestCase, TestCase

from accounts.models import User
from accounts.services.auth_throttle import (
    LOGIN_EMAIL_LIMIT_KEY,
    LOGIN_IP_LIMIT_KEY,
    LOGIN_WINDOW_KEY,
    MAIL_EMAIL_LIMIT_KEY,
    MAIL_IP_LIMIT_KEY,
    MAIL_WINDOW_KEY,
)
from integrations.settings_registry import INTEGRATION_GROUPS, SETTING_VALUE_TYPES

AUTH_THROTTLE_KEYS = (
    LOGIN_IP_LIMIT_KEY,
    LOGIN_EMAIL_LIMIT_KEY,
    LOGIN_WINDOW_KEY,
    MAIL_IP_LIMIT_KEY,
    MAIL_EMAIL_LIMIT_KEY,
    MAIL_WINDOW_KEY,
)

PURGE_KEYS = (
    "PURGE_UNVERIFIED_BATCH_SIZE",
    "PURGE_UNVERIFIED_MAX_BATCHES",
)

EXPECTED_DEFAULTS = {
    LOGIN_IP_LIMIT_KEY: "20",
    LOGIN_EMAIL_LIMIT_KEY: "10",
    LOGIN_WINDOW_KEY: "900",
    MAIL_IP_LIMIT_KEY: "8",
    MAIL_EMAIL_LIMIT_KEY: "3",
    MAIL_WINDOW_KEY: "3600",
    "PURGE_UNVERIFIED_BATCH_SIZE": "500",
    "PURGE_UNVERIFIED_MAX_BATCHES": "50",
}


class AuthThrottleRegistryTest(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.auth_group = next(
            group for group in INTEGRATION_GROUPS if group["name"] == "auth"
        )
        cls.entries = {entry["key"]: entry for entry in cls.auth_group["keys"]}

    def test_keys_are_integers_on_existing_auth_group(self):
        self.assertEqual(len(INTEGRATION_GROUPS), 17)
        for key in AUTH_THROTTLE_KEYS:
            self.assertIn(key, self.entries)
            entry = self.entries[key]
            self.assertFalse(entry.get("is_secret", False))
            self.assertTrue(entry.get("optional", False))
            self.assertEqual(entry.get("default"), EXPECTED_DEFAULTS[key])
            self.assertEqual(entry.get("value_type"), "integer")
            self.assertEqual(SETTING_VALUE_TYPES[key], "integer")
            self.assertEqual(
                entry.get("docs_url"),
                f"_docs/integrations/auth.md#{key.lower()}",
            )
            self.assertTrue(entry.get("description"))

    def test_docs_anchors_resolve_to_sections(self):
        auth_md = os.path.join(
            settings.BASE_DIR, "_docs", "integrations", "auth.md"
        )
        with open(auth_md, encoding="utf-8") as fh:
            content = fh.read()
        for key in AUTH_THROTTLE_KEYS:
            self.assertIn(
                f"## {key}\n",
                content,
                f"auth.md has no '## {key}' section",
            )
        configuration = os.path.join(
            settings.BASE_DIR, "_docs", "configuration.md"
        )
        with open(configuration, encoding="utf-8") as fh:
            config_text = fh.read()
        for key in AUTH_THROTTLE_KEYS:
            self.assertIn(f"`{key}`", config_text)

    def test_purge_limits_are_integer_auth_settings_with_docs(self):
        auth_md = os.path.join(
            settings.BASE_DIR, "_docs", "integrations", "auth.md"
        )
        with open(auth_md, encoding="utf-8") as fh:
            auth_text = fh.read()
        configuration = os.path.join(
            settings.BASE_DIR, "_docs", "configuration.md"
        )
        with open(configuration, encoding="utf-8") as fh:
            config_text = fh.read()

        for key in PURGE_KEYS:
            entry = self.entries[key]
            self.assertFalse(entry.get("is_secret", False))
            self.assertTrue(entry.get("optional", False))
            self.assertEqual(entry["default"], EXPECTED_DEFAULTS[key])
            self.assertEqual(entry["value_type"], "integer")
            self.assertEqual(SETTING_VALUE_TYPES[key], "integer")
            self.assertEqual(
                entry["docs_url"],
                f"_docs/integrations/auth.md#{key.lower()}",
            )
            self.assertIn(f"## {key}\n", auth_text)
            self.assertIn(f"`{key}`", config_text)


class AuthThrottleStudioSettingsTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff_user = User.objects.create_user(
            email="auth-throttle-admin@test.com",
            password="testpass",
            is_staff=True,
        )

    def test_studio_settings_renders_auth_throttle_keys(self):
        self.client.login(
            email="auth-throttle-admin@test.com", password="testpass"
        )
        response = self.client.get("/studio/settings/")
        for key in AUTH_THROTTLE_KEYS:
            self.assertContains(response, f'data-field-key="{key}"')

    def test_studio_settings_renders_purge_limit_keys(self):
        self.client.login(
            email="auth-throttle-admin@test.com", password="testpass"
        )
        response = self.client.get("/studio/settings/")
        for key in PURGE_KEYS:
            self.assertContains(response, f'data-field-key="{key}"')
