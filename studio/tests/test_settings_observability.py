"""Tests for the Studio Observability settings surface (issue #813).

Confirms:

- ``/studio/settings/`` exposes the new ``Observability`` group with the
  three Logfire keys and renders the token masked.
- POSTing to the save endpoint upserts the token and clears the config
  cache; an empty token deletes the row so the value falls back to
  env/default.
"""

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings

from integrations.config import clear_config_cache, get_config
from integrations.models import IntegrationSetting

User = get_user_model()

FAKE_TOKEN_ENV = 'pylf_fake_env_token'
FAKE_TOKEN_DB = 'pylf_fake_db_token'


class SettingsDashboardObservabilityTest(TestCase):

    def setUp(self):
        clear_config_cache()
        self.addCleanup(clear_config_cache)
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')

    def test_staff_sees_observability_group_with_all_three_keys(self):
        response = self.client.get('/studio/settings/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Observability')
        self.assertContains(response, 'LOGFIRE_TOKEN')
        self.assertContains(response, 'LOGFIRE_ENABLED')
        self.assertContains(response, 'LOGFIRE_ENVIRONMENT')

    def test_token_field_is_masked(self):
        response = self.client.get('/studio/settings/')
        # The secret field renders as a password input (masked), not text.
        self.assertContains(
            response,
            'type="password" id="field-LOGFIRE_TOKEN"',
        )

    def test_save_upserts_token_and_clears_cache(self):
        response = self.client.post(
            '/studio/settings/observability/save/',
            {
                'LOGFIRE_TOKEN': FAKE_TOKEN_DB,
                'LOGFIRE_ENABLED': 'true',
                'LOGFIRE_ENVIRONMENT': 'production',
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn('observability', response.url)
        self.assertEqual(
            IntegrationSetting.objects.get(key='LOGFIRE_TOKEN').value,
            FAKE_TOKEN_DB,
        )
        self.assertTrue(IntegrationSetting.objects.get(key='LOGFIRE_TOKEN').is_secret)
        # Cache cleared on save -> get_config reflects the new value.
        self.assertEqual(get_config('LOGFIRE_TOKEN'), FAKE_TOKEN_DB)

    @override_settings(LOGFIRE_TOKEN=FAKE_TOKEN_ENV)
    def test_empty_token_deletes_row_and_falls_back_to_env(self):
        IntegrationSetting.objects.create(
            key='LOGFIRE_TOKEN', value=FAKE_TOKEN_DB, is_secret=True,
            group='observability',
        )
        clear_config_cache()
        self.assertEqual(get_config('LOGFIRE_TOKEN'), FAKE_TOKEN_DB)

        self.client.post(
            '/studio/settings/observability/save/',
            {
                'LOGFIRE_TOKEN': '',
                'LOGFIRE_ENABLED': 'false',
                'LOGFIRE_ENVIRONMENT': '',
            },
        )
        self.assertFalse(
            IntegrationSetting.objects.filter(key='LOGFIRE_TOKEN').exists(),
        )
        # Falls back to the env/settings default value.
        self.assertEqual(get_config('LOGFIRE_TOKEN'), FAKE_TOKEN_ENV)
