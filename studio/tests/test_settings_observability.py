"""Tests for the Studio Observability settings surface (issue #813).

Confirms:

- ``/studio/settings/`` exposes the new ``Observability`` group with the
  three Logfire keys and renders the token masked.
- POSTing to the save endpoint upserts the token and clears the config
  cache; an empty token deletes the row so the value falls back to
  env/default.
"""

from community_base.config.models import Setting
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings

from integrations.config import (
    clear_config_cache,
    get_config,
    set_package_override,
)

User = get_user_model()

FAKE_TOKEN_ENV = "pylf_fake_env_token"
FAKE_TOKEN_DB = "pylf_fake_db_token"
RESTART_HINT = "Takes effect on the next web and worker process start. Saving does not reconfigure this process."
RESTART_MESSAGE = "Observability changes apply after you restart the web and worker processes."


class SettingsDashboardObservabilityTest(TestCase):
    def setUp(self):
        clear_config_cache()
        self.addCleanup(clear_config_cache)
        self.client = Client()
        self.staff = User.objects.create_user(
            email="staff@test.com",
            password="testpass",
            is_staff=True,
        )
        self.client.login(email="staff@test.com", password="testpass")

    def test_staff_sees_observability_group_with_all_three_keys(self):
        response = self.client.get("/studio/settings/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Observability")
        self.assertContains(response, "LOGFIRE_TOKEN")
        self.assertContains(response, "LOGFIRE_ENABLED")
        self.assertContains(response, "LOGFIRE_ENVIRONMENT")

    def test_token_field_is_masked(self):
        response = self.client.get("/studio/settings/")
        # The secret field renders as a password input (masked), not text.
        self.assertContains(
            response,
            'type="password" id="field-LOGFIRE_TOKEN"',
        )

    def test_restart_badges_and_hints_render_only_for_observability_fields(self):
        response = self.client.get("/studio/settings/")

        for key in (
            "LOGFIRE_ENABLED",
            "LOGFIRE_TOKEN",
            "LOGFIRE_ENVIRONMENT",
        ):
            with self.subTest(key=key):
                self.assertContains(
                    response,
                    f'data-requires-restart-badge="{key}"',
                )
                self.assertContains(
                    response,
                    f'data-requires-restart-hint="{key}"',
                )
        self.assertContains(response, RESTART_HINT, count=3)
        self.assertNotContains(
            response,
            'data-requires-restart-badge="CONTENT_CDN_BASE"',
        )

    def test_save_upserts_token_and_clears_cache(self):
        response = self.client.post(
            "/studio/settings/observability/save/",
            {
                "LOGFIRE_TOKEN": FAKE_TOKEN_DB,
                "LOGFIRE_ENABLED": "true",
                "LOGFIRE_ENVIRONMENT": "production",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("observability", response.url)
        # The token is a declared secret: the row holds ciphertext and the
        # donor-shaped plaintext only comes back through the shim read.
        self.assertTrue(
            Setting.objects.filter(key="LOGFIRE_TOKEN").exists(),
        )
        # Cache cleared on save -> get_config reflects the new value.
        self.assertEqual(get_config("LOGFIRE_TOKEN"), FAKE_TOKEN_DB)

    def test_save_flashes_persistence_and_restart_contract(self):
        response = self.client.post(
            "/studio/settings/observability/save/",
            {
                "LOGFIRE_TOKEN": FAKE_TOKEN_DB,
                "LOGFIRE_ENABLED": "true",
                "LOGFIRE_ENVIRONMENT": "production",
            },
            follow=True,
        )

        self.assertContains(response, "Saved 3 settings in Observability.")
        self.assertContains(response, RESTART_MESSAGE)

    def test_disabling_logfire_still_requires_a_restart(self):
        for key, value in (
            ("LOGFIRE_ENABLED", "true"),
            ("LOGFIRE_TOKEN", FAKE_TOKEN_DB),
            ("LOGFIRE_ENVIRONMENT", "production"),
        ):
            set_package_override(key, value, actor_ref="test")

        response = self.client.post(
            "/studio/settings/observability/save/",
            {
                "LOGFIRE_TOKEN": FAKE_TOKEN_DB,
                "LOGFIRE_ENVIRONMENT": "production",
            },
            follow=True,
        )

        self.assertEqual(get_config("LOGFIRE_ENABLED"), "false")
        self.assertContains(response, RESTART_MESSAGE)
        self.assertNotContains(response, "Tracing has stopped")

    def test_saving_site_group_does_not_flash_observability_restart(self):
        response = self.client.post(
            "/studio/settings/site/save/",
            {"SITE_BASE_URL": "https://example.test"},
            follow=True,
        )

        self.assertContains(response, "settings in Site.")
        self.assertNotContains(response, RESTART_MESSAGE)

    @override_settings(LOGFIRE_TOKEN=FAKE_TOKEN_ENV)
    def test_empty_token_deletes_row_and_falls_back_to_env(self):
        set_package_override(
            key="LOGFIRE_TOKEN",
            value=FAKE_TOKEN_DB,
            actor_ref="test",
        )
        clear_config_cache()
        self.assertEqual(get_config("LOGFIRE_TOKEN"), FAKE_TOKEN_DB)

        self.client.post(
            "/studio/settings/observability/save/",
            {
                "LOGFIRE_TOKEN": "",
                "LOGFIRE_ENABLED": "false",
                "LOGFIRE_ENVIRONMENT": "",
            },
        )
        self.assertFalse(
            Setting.objects.filter(key="LOGFIRE_TOKEN").exists(),
        )
        # Falls back to the env/settings default value.
        self.assertEqual(get_config("LOGFIRE_TOKEN"), FAKE_TOKEN_ENV)
