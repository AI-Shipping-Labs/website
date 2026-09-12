"""Compatibility and route checks for the A0.2 package cutover (#1584)."""

import json
from unittest.mock import patch

from community_base.api.models import APIKey
from community_base.config.crypto import PREFIX, decrypt
from community_base.config.models import Setting, SettingChange
from community_base.config.service import export as package_export
from community_base.config.service import set as package_set
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import resolve, reverse

from integrations.config import (
    delete_package_override,
    get_config,
    reset_local_config_cache,
    resolve_source,
    set_package_override,
)
from integrations.models import IntegrationSetting
from studio.services.settings_io import build_export

User = get_user_model()


class PackageStorageShimTest(TestCase):
    """The old import path resolves package rows during the transition."""

    def setUp(self):
        reset_local_config_cache()

    def tearDown(self):
        reset_local_config_cache()

    def test_package_row_wins_and_secret_round_trips_encrypted(self):
        IntegrationSetting.objects.create(
            key="STRIPE_SECRET_KEY",
            value="stale-donor-value",
            is_secret=True,
            group="stripe",
        )
        package_set(
            "STRIPE_SECRET_KEY",
            "synthetic-package-secret",
            actor_ref="test:1584",
            reason="cutover test",
        )
        row = Setting.objects.get(key="STRIPE_SECRET_KEY")

        self.assertTrue(row.value.startswith(PREFIX))
        self.assertNotIn("synthetic-package-secret", row.value)
        self.assertEqual(decrypt(row.value), "synthetic-package-secret")
        self.assertEqual(get_config("STRIPE_SECRET_KEY", "fallback"), "synthetic-package-secret")

    def test_package_secret_write_never_creates_plaintext_donor_row_or_export(self):
        secret = "synthetic-never-exported-secret"

        set_package_override(
            "STRIPE_SECRET_KEY",
            secret,
            actor_ref="test:1584",
            reason="secret leak regression",
        )

        self.assertFalse(
            IntegrationSetting.objects.filter(
                key="STRIPE_SECRET_KEY", value=secret
            ).exists()
        )
        payload = build_export()
        exported = json.dumps(payload)
        self.assertNotIn(secret, exported)
        self.assertEqual(package_export()["STRIPE_SECRET_KEY"], "[REDACTED]")
        self.assertEqual(
            next(
                item["value"]
                for item in payload["integration_settings"]
                if item["key"] == "STRIPE_SECRET_KEY"
            ),
            "[REDACTED]",
        )

    def test_donor_only_row_remains_available_until_second_pr(self):
        IntegrationSetting.objects.create(
            key="SLACK_INVITE_URL",
            value="https://example.test/invite",
            group="slack",
        )

        self.assertEqual(
            get_config("SLACK_INVITE_URL", "fallback"),
            "https://example.test/invite",
        )

    def test_legacy_secret_row_is_never_a_runtime_or_export_source(self):
        secret = "legacy-plaintext-must-stay-dark"
        IntegrationSetting.objects.create(
            key="UNDECLARED_LEGACY_SECRET",
            value=secret,
            is_secret=True,
            group="legacy",
        )

        self.assertEqual(
            get_config("UNDECLARED_LEGACY_SECRET", "safe-fallback"),
            "safe-fallback",
        )
        self.assertNotIn(secret, json.dumps(build_export()))

    def test_clear_tombstone_masks_stale_donor_across_resolution_paths(self):
        IntegrationSetting.objects.create(
            key="SITE_BASE_URL",
            value="https://stale-donor.example",
            group="site",
        )
        package_set(
            "SITE_BASE_URL",
            "https://override.example",
            actor_ref="test:1584",
        )
        self.assertEqual(get_config("SITE_BASE_URL", "fallback"), "https://override.example")

        self.assertTrue(delete_package_override("SITE_BASE_URL"))
        self.assertFalse(Setting.objects.filter(key="SITE_BASE_URL").exists())
        self.assertTrue(
            SettingChange.objects.filter(
                setting_key="SITE_BASE_URL", new_value__isnull=True
            ).exists()
        )
        with self.settings(SITE_BASE_URL="https://settings.example"):
            # Warm cache, cold cache, uncached worker resolution, and source
            # reporting must all ignore the stale donor row after a clear.
            self.assertEqual(get_config("SITE_BASE_URL", "fallback"), "https://settings.example")
            reset_local_config_cache()
            self.assertEqual(get_config("SITE_BASE_URL", "fallback"), "https://settings.example")
            with patch.dict("os.environ", {"DJANGO_QCLUSTER_PROCESS": "true"}):
                self.assertEqual(get_config("SITE_BASE_URL", "fallback"), "https://settings.example")
            self.assertEqual(resolve_source("SITE_BASE_URL"), "django_settings")

        # A later package write supersedes the tombstone and remains the
        # authoritative value even though the donor row still exists.
        package_set("SITE_BASE_URL", "https://new-package.example", actor_ref="test:1584")
        reset_local_config_cache()
        self.assertEqual(get_config("SITE_BASE_URL", "fallback"), "https://new-package.example")


class PackageSettingsRouteTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="package-settings-staff@test.com",
            password="pw",
            is_staff=True,
        )
        cls.api_key, cls.plaintext = APIKey.create_for_user(
            user=cls.staff,
            name="settings cutover",
            scopes=["settings.read", "settings.write"],
            kind=APIKey.Kind.STAFF,
        )

    def test_studio_settings_uses_package_view_and_alias_reverse(self):
        resolved = resolve("/studio/settings/")
        self.assertEqual(resolved.func.__module__, "community_base.config.views")
        self.assertEqual(reverse("studio_settings"), "/studio/settings/")

    def test_package_api_key_management_is_mounted_in_studio(self):
        self.assertEqual(reverse("community_base_api_keys"), "/studio/api-keys/")
        self.assertEqual(
            resolve("/studio/api-keys/").func.__module__,
            "community_base.api.views",
        )

    def test_package_operator_routes_are_mounted_under_versioned_api_prefix(self):
        self.assertEqual(
            resolve("/api/v1/settings").func.__module__,
            "community_base.api.registry",
        )

    def test_studio_page_renders_package_source_badges(self):
        self.client.force_login(self.staff)
        response = self.client.get("/studio/settings/")

        self.assertContains(response, "analytics")
        self.assertContains(response, 'data-settings-card-type="integration"')
        self.assertContains(response, "Source: default")

    def test_studio_settings_page_keeps_package_source_badges(self):
        self.client.force_login(self.staff)
        response = self.client.get("/studio/settings/")

        self.assertContains(response, 'data-settings-source="GOOGLE_ANALYTICS_ID"')
        self.assertContains(response, "default")

    def test_package_form_skips_blank_optional_integer(self):
        self.client.force_login(self.staff)
        package_set("USER_ACTIVITY_RETENTION_DAYS", 30, actor_ref="test:1584")
        response = self.client.post(
            "/studio/settings/analytics/save/",
            {
                "GOOGLE_ANALYTICS_ID": "",
                "USER_ACTIVITY_RETENTION_DAYS": "",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Setting.objects.filter(key="USER_ACTIVITY_RETENTION_DAYS").exists())
        self.assertTrue(
            SettingChange.objects.filter(
                setting_key="USER_ACTIVITY_RETENTION_DAYS", new_value__isnull=True
            ).exists()
        )

    def test_package_api_requires_bearer_key_and_lists_without_values(self):
        unauthorized = self.client.get("/api/v1/settings")
        self.assertEqual(unauthorized.status_code, 401)

        response = self.client.get(
            "/api/v1/settings?limit=100",
            HTTP_AUTHORIZATION=f"Bearer {self.plaintext}",
        )
        payload = response.json()
        self.assertIn("settings", payload)
        self.assertIn("pagination", payload)
        self.assertTrue(any(item["key"] == "GOOGLE_ANALYTICS_ID" for item in payload["settings"]))

    def test_package_api_import_writes_package_storage(self):
        response = self.client.post(
            "/api/v1/settings/import",
            data=json.dumps(
                {
                    "settings": {"SITE_BASE_URL": "https://api.example.test"},
                    "reason": "test import",
                }
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.plaintext}",
        )

        self.assertEqual(response.json(), {"updated": ["SITE_BASE_URL"]})
        self.assertEqual(Setting.objects.get(key="SITE_BASE_URL").source, "import")
        # The package view schedules its runtime stamp with ``on_commit``;
        # Django TestCase keeps the outer transaction open until teardown.
        # Reset the shim cache so this assertion reads the committed row.
        reset_local_config_cache()
        self.assertEqual(get_config("SITE_BASE_URL", "fallback"), "https://api.example.test")
