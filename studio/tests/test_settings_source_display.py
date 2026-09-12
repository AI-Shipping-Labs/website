"""Source badges for package-owned settings."""

import os
from unittest.mock import patch

from community_base.config.models import Setting
from community_base.config.service import set as package_set
from django.contrib.auth import get_user_model
from django.test import TestCase

from integrations.config import clear_config_cache

User = get_user_model()


class SettingsSourceBadgeTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(email="source-staff@test.com", password="testpass", is_staff=True)

    def setUp(self):
        self.client.login(email=self.staff.email, password="testpass")
        clear_config_cache()

    def tearDown(self):
        Setting.objects.filter(key__in=("SITE_BASE_URL", "STRIPE_SECRET_KEY")).delete()
        clear_config_cache()

    def _setting(self, response, key):
        return next(
            setting
            for group in response.context["groups"]
            for setting in group["settings"]
            if setting["key"] == key
        )

    def test_default_source_is_rendered_without_a_package_override(self):
        with patch.dict(os.environ):
            os.environ.pop("SITE_BASE_URL", None)
            response = self.client.get("/studio/settings/")
        setting = self._setting(response, "SITE_BASE_URL")

        self.assertEqual(setting["source"], "default")
        self.assertContains(response, 'data-settings-source="SITE_BASE_URL">Source: default</span>')

    def test_package_override_gets_db_source_badge(self):
        package_set("SITE_BASE_URL", "https://package.example.test", actor_ref="test:1584")
        response = self.client.get("/studio/settings/")
        setting = self._setting(response, "SITE_BASE_URL")

        self.assertEqual(setting["source"], "db")
        self.assertContains(response, 'data-settings-source="SITE_BASE_URL">Source: db</span>')

    def test_secret_override_never_appears_in_rendered_html(self):
        package_set("STRIPE_SECRET_KEY", "secret-source-value", actor_ref="test:1584")
        response = self.client.get("/studio/settings/")

        self.assertNotIn("secret-source-value", response.content.decode())
        self.assertNotContains(response, 'value="secret-source-value"')
