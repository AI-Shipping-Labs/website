"""Site configuration contracts for package settings (#369, #1584)."""

import os
from unittest.mock import patch

from community_base.config.models import Setting
from community_base.config.service import set as package_set
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from integrations.config import clear_config_cache, get_config, site_base_url
from integrations.settings_registry import get_group_by_name

User = get_user_model()


class SiteGroupRegistryTest(TestCase):
    def test_site_group_has_expected_keys(self):
        group = get_group_by_name("site")
        self.assertEqual(group["label"], "Site")
        keys = {item["key"] for item in group["keys"]}
        self.assertTrue({"SITE_BASE_URL", "SITE_BASE_URL_ALIASES", "EVENT_DISPLAY_TIMEZONE"} <= keys)
        self.assertTrue(all(item["is_secret"] is False for item in group["keys"]))


class SiteBaseUrlConfigTest(TestCase):
    def setUp(self):
        clear_config_cache()

    def tearDown(self):
        Setting.objects.filter(key__in=("SITE_BASE_URL", "SITE_BASE_URL_ALIASES")).delete()
        clear_config_cache()

    def test_package_value_overrides_environment(self):
        package_set("SITE_BASE_URL", "https://package.example.test", actor_ref="test:1584")
        with patch.dict(os.environ, {"SITE_BASE_URL": "https://env.example.test"}):
            clear_config_cache()
            self.assertEqual(site_base_url(), "https://package.example.test")

    @override_settings(SITE_BASE_URL=None)
    def test_environment_is_used_when_package_row_is_absent(self):
        with patch.dict(os.environ, {"SITE_BASE_URL": "https://env.example.test"}):
            clear_config_cache()
            self.assertEqual(get_config("SITE_BASE_URL", "fallback"), "https://env.example.test")

    def test_package_write_is_visible_after_cache_clear(self):
        self.assertEqual(get_config("SITE_BASE_URL_ALIASES", "fallback"), "fallback")
        package_set("SITE_BASE_URL_ALIASES", "old.example.test", actor_ref="test:1584")
        clear_config_cache()
        self.assertEqual(get_config("SITE_BASE_URL_ALIASES", "fallback"), "old.example.test")


class SitePackageStorageTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(email="site-settings@test.com", password="testpass", is_staff=True)

    def setUp(self):
        self.client.login(email=self.staff.email, password="testpass")

    def tearDown(self):
        Setting.objects.filter(key__in=("SITE_BASE_URL", "SITE_BASE_URL_ALIASES", "EVENT_DISPLAY_TIMEZONE")).delete()

    def test_package_settings_page_is_canonical(self):
        response = self.client.get("/studio/settings/")
        self.assertEqual(response.context["groups"][0]["name"], "analytics")

    def test_site_values_are_package_managed(self):
        package_set("SITE_BASE_URL", "https://site.example.test", actor_ref="test:1584")
        response = self.client.get("/studio/settings/")
        body = response.content.decode()
        self.assertIn('data-settings-section="site"', body)
        self.assertIn('value="https://site.example.test"', body)
        self.assertEqual(Setting.objects.get(key="SITE_BASE_URL").source, "studio")
