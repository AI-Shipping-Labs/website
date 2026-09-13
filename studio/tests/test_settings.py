"""Studio settings contracts for the community-base package (#1584)."""

import re
from pathlib import Path

import pytest
from community_base.config.models import Setting
from community_base.config.service import set as package_set
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from integrations.config import clear_config_cache

User = get_user_model()


class SettingsSemanticPaletteTest(SimpleTestCase):
    @pytest.mark.visual_regression
    def test_package_template_uses_semantic_palette_tokens(self):
        source = (
            Path(__file__).resolve().parents[2] / "templates" / "community_base" / "config" / "settings.html"
        ).read_text()
        self.assertNotRegex(source, r"(?:bg|border|text|placeholder)-gray-")
        self.assertIn("bg-background", source)
        self.assertIn("text-foreground", source)


class PackageSettingsFormTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="studio-settings@test.com", password="testpass", is_staff=True
        )

    def setUp(self):
        self.client.login(email=self.staff.email, password="testpass")
        clear_config_cache()

    def tearDown(self):
        Setting.objects.filter(key__in=("SLACK_ENABLED", "SLACK_BOT_TOKEN", "EMAIL_BATCH_SIZE")).delete()
        clear_config_cache()

    def test_boolean_setting_renders_as_checkbox(self):
        response = self.client.get("/studio/settings/")
        match = re.search(r'<input[^>]*id="id_SLACK_ENABLED"[^>]*>', response.content.decode())
        self.assertIsNotNone(match)
        self.assertIn('type="checkbox"', match.group(0))

    def test_secret_setting_is_password_input(self):
        response = self.client.get("/studio/settings/")
        match = re.search(r'<input[^>]*id="id_SLACK_BOT_TOKEN"[^>]*>', response.content.decode())
        self.assertIsNotNone(match)
        self.assertIn('type="password"', match.group(0))

    def test_package_override_is_encrypted_and_runtime_visible(self):
        package_set("SLACK_BOT_TOKEN", "secret-token", actor_ref="test:1584")
        response = self.client.get("/studio/settings/")
        row = Setting.objects.get(key="SLACK_BOT_TOKEN")

        self.assertNotIn("secret-token", response.content.decode())
        self.assertNotEqual(row.value, "secret-token")

    def test_invalid_integer_does_not_write_package_row(self):
        response = self.client.post(
            "/studio/settings/ses/save/", {"EMAIL_BATCH_SIZE": "not-an-integer"}
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Setting.objects.filter(key="EMAIL_BATCH_SIZE").exists())

    def test_unknown_group_does_not_write_settings(self):
        response = self.client.post("/studio/settings/unknown/save/", {"ANY_KEY": "value"})

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Setting.objects.filter(key="ANY_KEY").exists())
