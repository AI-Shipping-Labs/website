"""Tests for Calendly config resolution (issue #884).

Every Calendly setting must flow through the IntegrationSetting
framework so it is Studio-editable with DB > env > default precedence,
and every key must be registered so it appears in Studio settings.
"""

from django.test import TestCase, tag

from community.calendly_config import (
    calendly_webhook_validation_enabled,
    get_calendly_access_token,
    get_calendly_webhook_signing_key,
)
from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting
from integrations.settings_registry import get_group_by_name


@tag('core')
class CalendlyConfigTest(TestCase):
    def tearDown(self):
        clear_config_cache()

    def test_access_token_reads_db_override(self):
        IntegrationSetting.objects.create(
            key='CALENDLY_ACCESS_TOKEN', value='tok-1', group='calendly',
        )
        clear_config_cache()
        self.assertEqual(get_calendly_access_token(), 'tok-1')

    def test_access_token_defaults_blank(self):
        clear_config_cache()
        self.assertEqual(get_calendly_access_token(), '')

    def test_signing_key_reads_db_override(self):
        IntegrationSetting.objects.create(
            key='CALENDLY_WEBHOOK_SIGNING_KEY', value='sk-1', group='calendly',
        )
        clear_config_cache()
        self.assertEqual(get_calendly_webhook_signing_key(), 'sk-1')

    def test_validation_flag_defaults_false(self):
        clear_config_cache()
        self.assertFalse(calendly_webhook_validation_enabled())

    def test_validation_flag_reads_db_override(self):
        IntegrationSetting.objects.create(
            key='CALENDLY_WEBHOOK_VALIDATION_ENABLED', value='true', group='calendly',
        )
        clear_config_cache()
        self.assertTrue(calendly_webhook_validation_enabled())


@tag('core')
class CalendlyRegistryTest(TestCase):
    def test_calendly_group_registered_with_expected_keys(self):
        group = get_group_by_name('calendly')
        self.assertIsNotNone(group)
        keys = {k['key'] for k in group['keys']}
        self.assertEqual(
            keys,
            {
                'CALENDLY_ACCESS_TOKEN',
                'CALENDLY_WEBHOOK_SIGNING_KEY',
                'CALENDLY_OAUTH_CLIENT_ID',
                'CALENDLY_OAUTH_CLIENT_SECRET',
                'CALENDLY_WEBHOOK_VALIDATION_ENABLED',
            },
        )

    def test_every_calendly_key_has_description_and_docs_url(self):
        group = get_group_by_name('calendly')
        for key_def in group['keys']:
            self.assertTrue(key_def.get('description'), key_def['key'])
            self.assertTrue(key_def.get('docs_url'), key_def['key'])
