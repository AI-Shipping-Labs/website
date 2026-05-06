"""Studio settings dashboard must render the resolved ``SITE_BASE_URL``
(DB override > env) — both in the Site card and in the OAuth callback
URL preview (issue #435).
"""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting

User = get_user_model()


@override_settings(SITE_BASE_URL='https://env.example.com')
class SettingsDashboardSiteUrlOverrideTest(TestCase):
    """``settings_dashboard`` view passes the resolved value to both
    ``get_all_auth_providers`` and the template context."""

    @classmethod
    def setUpTestData(cls):
        cls.staff_user = User.objects.create_user(
            email='admin@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        clear_config_cache()
        self.client.login(email='admin@test.com', password='testpass')

    def tearDown(self):
        clear_config_cache()

    def test_dashboard_passes_override_to_auth_providers(self):
        IntegrationSetting.objects.create(
            key='SITE_BASE_URL',
            value='https://override.example.com',
            group='site',
        )
        clear_config_cache()
        response = self.client.get('/studio/settings/')
        self.assertEqual(response.status_code, 200)
        # The Site-card context value is the override.
        self.assertEqual(
            response.context['site_base_url'],
            'https://override.example.com',
        )
        # Each auth provider's callback URL is built from the same
        # resolved value. Sanity-check the rendered Google card carries
        # the override host.
        providers = response.context['auth_providers']
        google = next(
            (p for p in providers if p.get('provider') == 'google'),
            None,
        )
        self.assertIsNotNone(google)
        self.assertTrue(
            google['callback_url'].startswith(
                'https://override.example.com/'
            ),
            f'Unexpected callback URL: {google["callback_url"]!r}',
        )

    def test_dashboard_falls_back_to_env_when_no_override(self):
        # Regression guard.
        self.assertFalse(
            IntegrationSetting.objects.filter(key='SITE_BASE_URL').exists()
        )
        response = self.client.get('/studio/settings/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context['site_base_url'],
            'https://env.example.com',
        )
