"""Tests for the Studio banner_generator settings surface (issue #788).

Confirms:

- ``/studio/settings/`` exposes the new ``Content Tools`` section.
- The ``banner_generator`` group renders both fields.
- POSTing to the save endpoint upserts both IntegrationSetting rows and
  clears the config cache.
"""

from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting

User = get_user_model()


class SettingsDashboardBannerGeneratorTest(TestCase):

    def setUp(self):
        clear_config_cache()
        self.addCleanup(clear_config_cache)
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.normal = User.objects.create_user(
            email='user@test.com', password='testpass',
        )

    def test_anonymous_redirects_to_login(self):
        response = self.client.get('/studio/settings/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)

    def test_non_staff_user_gets_403(self):
        self.client.login(email='user@test.com', password='testpass')
        response = self.client.get('/studio/settings/')
        self.assertEqual(response.status_code, 403)

    def test_staff_sees_content_tools_section(self):
        self.client.login(email='staff@test.com', password='testpass')
        response = self.client.get('/studio/settings/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Content Tools')
        self.assertContains(response, 'BANNER_GENERATOR_FUNCTION_URL')
        self.assertContains(response, 'BANNER_GENERATOR_AUTH_TOKEN')

    def test_save_upserts_both_keys(self):
        self.client.login(email='staff@test.com', password='testpass')
        response = self.client.post(
            '/studio/settings/banner_generator/save/',
            {
                'BANNER_GENERATOR_FUNCTION_URL': 'https://lambda.example.com/render',
                'BANNER_GENERATOR_AUTH_TOKEN': 'token-zzz',
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn('content_tools', response.url)
        self.assertEqual(
            IntegrationSetting.objects.get(
                key='BANNER_GENERATOR_FUNCTION_URL',
            ).value,
            'https://lambda.example.com/render',
        )
        self.assertEqual(
            IntegrationSetting.objects.get(
                key='BANNER_GENERATOR_AUTH_TOKEN',
            ).value,
            'token-zzz',
        )

    def test_save_clears_config_cache(self):
        self.client.login(email='staff@test.com', password='testpass')
        self.client.post(
            '/studio/settings/banner_generator/save/',
            {
                'BANNER_GENERATOR_FUNCTION_URL': 'https://lambda.example.com/render',
                'BANNER_GENERATOR_AUTH_TOKEN': 'token-zzz',
            },
        )
        # is_enabled() must see the saved values without a process restart.
        from integrations.services.banner_generator import is_enabled
        self.assertTrue(is_enabled())
