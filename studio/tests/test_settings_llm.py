"""Tests for the Studio LLM-provider settings surface (issue #799).

Confirms:

- ``/studio/settings/`` exposes the new ``AI`` section with the four
  ``LLM_*`` fields and renders the API key masked.
- POSTing to the save endpoint upserts non-empty package config
  overrides (the API key encrypted at rest), clears the config cache, and
  an empty key deletes the override so the value falls back to
  env/default.
"""

from community_base.config.models import Setting
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings

from integrations.config import clear_config_cache, get_config, set_package_override

User = get_user_model()

# Obvious fake; never a real key.
FAKE_KEY_A = "sk-test-fake-env-A"
FAKE_KEY_B = "sk-test-fake-db-B"


class SettingsDashboardLLMTest(TestCase):
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

    def test_staff_sees_ai_section_with_all_four_fields(self):
        response = self.client.get("/studio/settings/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "LLM Provider")
        self.assertContains(response, "LLM_PROVIDER")
        self.assertContains(response, "LLM_API_KEY")
        self.assertContains(response, "LLM_BASE_URL")
        self.assertContains(response, "LLM_MODEL")

    def test_api_key_field_is_masked(self):
        response = self.client.get("/studio/settings/")
        # The secret field renders as a password input (masked), not text.
        self.assertContains(
            response,
            'type="password" id="field-LLM_API_KEY"',
        )

    def test_save_upserts_nonempty_rows_and_clears_cache(self):
        response = self.client.post(
            "/studio/settings/llm/save/",
            {
                "LLM_PROVIDER": "anthropic",
                "LLM_API_KEY": FAKE_KEY_B,
                "LLM_BASE_URL": "https://gateway.example/v1",
                "LLM_MODEL": "claude-opus-4-1",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("ai", response.url)
        # The API key is a declared secret: the row holds ciphertext and
        # the plaintext only comes back through the shim read.
        self.assertTrue(Setting.objects.filter(key="LLM_API_KEY").exists())
        self.assertEqual(get_config("LLM_API_KEY"), FAKE_KEY_B)
        self.assertEqual(get_config("LLM_MODEL"), "claude-opus-4-1")

    @override_settings(LLM_API_KEY=FAKE_KEY_A)
    def test_empty_key_deletes_row_and_falls_back_to_env(self):
        set_package_override("LLM_API_KEY", FAKE_KEY_B, actor_ref="test")
        clear_config_cache()
        self.assertEqual(get_config("LLM_API_KEY"), FAKE_KEY_B)

        self.client.post(
            "/studio/settings/llm/save/",
            {
                "LLM_PROVIDER": "anthropic",
                "LLM_API_KEY": "",
                "LLM_BASE_URL": "",
                "LLM_MODEL": "",
            },
        )
        self.assertFalse(
            Setting.objects.filter(key="LLM_API_KEY").exists(),
        )
        # Falls back to the env/settings default value.
        self.assertEqual(get_config("LLM_API_KEY"), FAKE_KEY_A)
