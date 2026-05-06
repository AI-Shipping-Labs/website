"""Unit tests for ``integrations.config.site_base_url`` (issue #435).

The resolver replaces every direct ``settings.SITE_BASE_URL`` read with
a single helper that respects Studio DB overrides. These tests cover
its precedence rules in isolation; per-consumer behaviour is asserted
in the consumer test files.
"""

from django.test import TestCase, override_settings

from integrations.config import clear_config_cache, site_base_url
from integrations.models import IntegrationSetting


class SiteBaseUrlResolverTest(TestCase):
    """Resolver returns DB override when present, else Django settings."""

    def setUp(self):
        clear_config_cache()

    def tearDown(self):
        clear_config_cache()

    @override_settings(SITE_BASE_URL='https://env.example.com')
    def test_returns_settings_value_when_no_db_row(self):
        # No IntegrationSetting row => fall back to settings.
        self.assertFalse(
            IntegrationSetting.objects.filter(key='SITE_BASE_URL').exists()
        )
        self.assertEqual(site_base_url(), 'https://env.example.com')

    @override_settings(SITE_BASE_URL='https://env.example.com')
    def test_db_override_beats_settings_value(self):
        IntegrationSetting.objects.create(
            key='SITE_BASE_URL',
            value='https://override.example.com',
            group='site',
        )
        clear_config_cache()
        self.assertEqual(site_base_url(), 'https://override.example.com')

    @override_settings(SITE_BASE_URL='https://env.example.com')
    def test_empty_db_value_falls_back_to_settings(self):
        # Studio normally deletes rows with empty values, but the
        # resolver must treat an empty/falsy DB value as "no override"
        # to match get_config's `_cache[key]` truthy guard.
        IntegrationSetting.objects.create(
            key='SITE_BASE_URL',
            value='',
            group='site',
        )
        clear_config_cache()
        self.assertEqual(site_base_url(), 'https://env.example.com')
