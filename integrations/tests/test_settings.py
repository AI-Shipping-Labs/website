"""Contracts for the package-owned runtime settings cutover (#1584).

The donor settings dashboard and API were retired. These tests cover the
remaining compatibility resolver plus the package UI contract: package rows
are authoritative, legacy non-secret rows are read-only fallback, and secret
legacy rows are never runtime or export sources.
"""

import json
import os
from unittest.mock import patch

from community_base.config.models import Setting
from community_base.config.service import set as package_set
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import resolve, reverse

from integrations.config import clear_config_cache, get_config
from integrations.models import IntegrationSetting
from integrations.settings_registry import INTEGRATION_GROUPS
from studio.services.settings_io import build_export

User = get_user_model()


class RuntimeConfigResolverTest(TestCase):
    def setUp(self):
        clear_config_cache()

    def tearDown(self):
        clear_config_cache()

    def test_package_value_wins_over_stale_donor_value(self):
        IntegrationSetting.objects.create(
            key="SLACK_INVITE_URL", value="https://legacy.example/invite", group="slack"
        )
        package_set(
            "SLACK_INVITE_URL",
            "https://package.example/invite",
            actor_ref="test:1584",
            reason="resolver contract",
        )

        self.assertEqual(
            get_config("SLACK_INVITE_URL", "fallback"),
            "https://package.example/invite",
        )

    def test_non_secret_donor_row_is_read_only_fallback(self):
        IntegrationSetting.objects.create(
            key="UNDECLARED_LEGACY_SETTING",
            value="legacy-value",
            group="legacy",
            is_secret=False,
        )

        self.assertEqual(
            get_config("UNDECLARED_LEGACY_SETTING", "fallback"), "legacy-value"
        )
        self.assertEqual(
            Setting.objects.filter(key="UNDECLARED_LEGACY_SETTING").count(), 0
        )

    def test_legacy_secret_row_is_never_runtime_or_export_source(self):
        secret = "legacy-plaintext-must-never-resolve"
        IntegrationSetting.objects.create(
            key="UNDECLARED_LEGACY_SECRET",
            value=secret,
            group="legacy",
            is_secret=True,
        )

        self.assertEqual(
            get_config("UNDECLARED_LEGACY_SECRET", "safe-fallback"), "safe-fallback"
        )
        self.assertNotIn(secret, json.dumps(build_export()))

    @override_settings(UNDECLARED_CONFIG_VALUE="from-django-settings")
    def test_settings_layer_is_used_when_database_has_no_row(self):
        self.assertEqual(
            get_config("UNDECLARED_CONFIG_VALUE", "fallback"),
            "from-django-settings",
        )

    @override_settings(UNDECLARED_CONFIG_VALUE=None)
    def test_environment_layer_is_used_after_database_and_settings(self):
        with patch.dict(os.environ, {"UNDECLARED_CONFIG_VALUE": "from-env"}):
            self.assertEqual(get_config("UNDECLARED_CONFIG_VALUE", "fallback"), "from-env")


class PackageSettingsPageTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="package-settings-staff@test.com",
            password="testpass",
            is_staff=True,
        )
        cls.member = User.objects.create_user(
            email="package-settings-member@test.com", password="testpass"
        )

    def setUp(self):
        clear_config_cache()

    def tearDown(self):
        clear_config_cache()

    def test_settings_page_requires_staff_and_uses_package_view(self):
        response = self.client.get("/studio/settings/")
        self.assertEqual(response.status_code, 302)
        self.client.login(email=self.member.email, password="testpass")
        self.assertEqual(self.client.get("/studio/settings/").status_code, 403)
        self.client.login(email=self.staff.email, password="testpass")
        response = self.client.get("/studio/settings/")

        self.assertEqual(resolve("/studio/settings/").func.__module__, "community_base.config.views")
        self.assertTrue(response.context["groups"])
        self.assertContains(response, 'data-settings-card-type="integration"')

    def test_page_exposes_package_groups_and_source_metadata(self):
        self.client.login(email=self.staff.email, password="testpass")
        with patch.dict(os.environ):
            os.environ.pop("SLACK_ENABLED", None)
            response = self.client.get("/studio/settings/")
        groups = response.context["groups"]
        group = next(item for item in groups if item["name"] == "slack")
        setting = next(item for item in group["settings"] if item["key"] == "SLACK_ENABLED")

        self.assertEqual(setting["source"], "default")
        self.assertContains(response, 'data-settings-source="SLACK_ENABLED">Source: default</span>')

    def test_package_group_save_does_not_write_donor_storage(self):
        self.client.login(email=self.staff.email, password="testpass")
        response = self.client.post(
            reverse("studio_settings_save", kwargs={"group": "zoom"}),
            {"ZOOM_CLIENT_ID": "package-client-id"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(get_config("ZOOM_CLIENT_ID", "fallback"), "package-client-id")
        self.assertFalse(IntegrationSetting.objects.filter(key="ZOOM_CLIENT_ID").exists())

    def test_invalid_package_value_does_not_create_override(self):
        self.client.login(email=self.staff.email, password="testpass")
        response = self.client.post(
            reverse("studio_settings_save", kwargs={"group": "ses"}),
            {"EMAIL_BATCH_SIZE": "not-an-integer"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Setting.objects.filter(key="EMAIL_BATCH_SIZE").exists())


class RegistryContractTest(SimpleTestCase):
    def test_registered_keys_have_descriptions_and_docs(self):
        for group in INTEGRATION_GROUPS:
            for definition in group["keys"]:
                with self.subTest(key=definition["key"]):
                    self.assertTrue(definition["description"])
                    self.assertTrue(definition["docs_url"])

    def test_secret_definitions_are_explicit(self):
        for group in INTEGRATION_GROUPS:
            for definition in group["keys"]:
                with self.subTest(key=definition["key"]):
                    self.assertIsInstance(definition.get("is_secret", False), bool)
