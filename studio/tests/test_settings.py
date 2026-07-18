"""Tests for boolean integration setting rendering and save round-trip (issue #340).

Two keys in the integration registry are booleans stored as the literal
strings ``"true"`` / ``"false"`` — ``SES_WEBHOOK_VALIDATION_ENABLED`` and
``SLACK_ENABLED``. The studio settings form renders them as real HTML
checkboxes so the operator cannot typo ``True``, ``1`` or ``yes``. The
save view normalises the POST payload back to the same lowercase strings
so all existing parsers keep working.
"""

import re
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from integrations.models import IntegrationSetting
from integrations.settings_registry import (
    INTEGRATION_GROUPS,
    SETTING_VALUE_TYPES,
)

User = get_user_model()


class SettingsSemanticPaletteTest(SimpleTestCase):
    def test_filter_and_integration_card_use_semantic_palette_tokens(self):
        templates = Path(__file__).resolve().parents[2] / 'templates' / 'studio'
        for relative in (
            'settings/dashboard.html',
            'settings/_integration_card.html',
        ):
            with self.subTest(relative=relative):
                source = (templates / relative).read_text()
                self.assertNotRegex(
                    source,
                    r'(?:bg|border|text|placeholder)-gray-',
                )
                self.assertIn('bg-background', source)
                self.assertIn('text-foreground', source)


class BooleanSettingRoundTripTest(TestCase):
    """Boolean keys round-trip through the settings form as ``"true"``/``"false"``."""

    @classmethod
    def setUpTestData(cls):
        cls.staff_user = User.objects.create_user(
            email='admin@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='admin@test.com', password='testpass')

    def test_checkbox_checked_stores_true(self):
        self.client.post('/studio/settings/slack/save/', {
            'SLACK_ENABLED': 'true',
            'SLACK_BOT_TOKEN': 'xoxb-test',
        })
        self.assertEqual(
            IntegrationSetting.objects.get(key='SLACK_ENABLED').value,
            'true',
        )

    def test_checkbox_unchecked_stores_false_not_empty(self):
        # Pre-existing "true" row simulates the operator unticking a
        # previously enabled flag — the unchecked HTML checkbox sends no
        # SLACK_ENABLED key at all, and the save view must normalise that
        # absence to "false" rather than empty string.
        IntegrationSetting.objects.create(
            key='SLACK_ENABLED', value='true', is_secret=False, group='slack',
        )
        self.client.post('/studio/settings/slack/save/', {
            # SLACK_ENABLED deliberately absent.
            'SLACK_BOT_TOKEN': 'xoxb-test',
        })
        self.assertEqual(
            IntegrationSetting.objects.get(key='SLACK_ENABLED').value,
            'false',
        )

    def test_dashboard_renders_checkbox_for_boolean_key(self):
        response = self.client.get('/studio/settings/')
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()

        # The SLACK_ENABLED input must be a checkbox carrying value="true".
        slack_enabled = re.search(
            r'<input[^>]*name="SLACK_ENABLED"[^>]*>',
            body,
        )
        self.assertIsNotNone(slack_enabled)
        self.assertIn('type="checkbox"', slack_enabled.group(0))
        self.assertIn('value="true"', slack_enabled.group(0))

        # Non-boolean SLACK_BOT_TOKEN in the same group must NOT render as a checkbox.
        slack_bot_token = re.search(
            r'<input[^>]*name="SLACK_BOT_TOKEN"[^>]*>',
            body,
        )
        self.assertIsNotNone(slack_bot_token)
        self.assertNotIn('type="checkbox"', slack_bot_token.group(0))

    def test_stored_mixed_case_true_renders_checked(self):
        IntegrationSetting.objects.create(
            key='SLACK_ENABLED', value='True', is_secret=False, group='slack',
        )
        response = self.client.get('/studio/settings/')
        # Locate the SLACK_ENABLED input element and confirm it carries the
        # ``checked`` attribute, demonstrating the case-insensitive match.
        match = re.search(
            r'<input[^>]*name="SLACK_ENABLED"[^>]*>',
            response.content.decode(),
        )
        self.assertIsNotNone(match)
        self.assertIn('checked', match.group(0))


class EmailSesSenderSettingsTest(TestCase):
    """Email (SES) exposes separate sender fields for both email classes."""

    @classmethod
    def setUpTestData(cls):
        cls.staff_user = User.objects.create_user(
            email='ses-admin@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='ses-admin@test.com', password='testpass')

    def test_dashboard_renders_transactional_and_promotional_sender_fields(self):
        response = self.client.get('/studio/settings/')
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()

        self.assertIn('name="SES_TRANSACTIONAL_FROM_EMAIL"', body)
        self.assertIn('name="SES_PROMOTIONAL_FROM_EMAIL"', body)
        self.assertIn('required account and service email', body)
        self.assertIn('campaigns, newsletters, and marketing email', body)
        self.assertIn('Must be verified in SES', body)


class TypedSettingRegistryTest(TestCase):
    def test_representative_scalar_types_are_explicit(self):
        definitions = {
            key_def['key']: key_def
            for group in INTEGRATION_GROUPS
            for key_def in group['keys']
        }
        expected = {
            'SITE_BASE_URL': 'url',
            'STRIPE_CUSTOMER_PORTAL_URL': 'url',
            'EMAIL_BATCH_SIZE': 'integer',
            'CHECKOUT_BINDING_TTL_MINUTES': 'integer',
            'SLACK_ENABLED': 'boolean',
            'ONBOARDING_AI_ENABLED': 'boolean',
        }
        for key, value_type in expected.items():
            with self.subTest(key=key):
                self.assertEqual(definitions[key]['value_type'], value_type)

        self.assertNotIn('STRIPE_PAYMENT_LINKS', SETTING_VALUE_TYPES)
        self.assertNotIn('SITE_BASE_URL_ALIASES', SETTING_VALUE_TYPES)
        self.assertNotIn('CONTENT_CDN_BASE', SETTING_VALUE_TYPES)

    def test_every_boolean_registry_key_has_boolean_value_type(self):
        for group in INTEGRATION_GROUPS:
            for key_def in group['keys']:
                if key_def.get('is_boolean'):
                    with self.subTest(key=key_def['key']):
                        self.assertEqual(key_def.get('value_type'), 'boolean')


class AtomicTypedSettingSaveTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff_user = User.objects.create_user(
            email='typed-settings@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client.login(email=self.staff_user.email, password='testpass')

    def _seed(self, key, value, group):
        return IntegrationSetting.objects.create(
            key=key, value=value, group=group, is_secret=False,
        )

    def _assert_unchanged_after_invalid(self, group, payload, key, type_label, section):
        before = dict(
            IntegrationSetting.objects.filter(group=group).values_list('key', 'value')
        )
        response = self.client.post(
            f'/studio/settings/{group}/save/', payload, follow=True,
        )
        self.assertRedirects(
            response,
            f'/studio/settings/#{section}',
            fetch_redirect_response=False,
        )
        self.assertContains(
            response,
            f'{key} must be a valid {type_label}. No settings were saved.',
        )
        self.assertEqual(
            dict(IntegrationSetting.objects.filter(group=group).values_list('key', 'value')),
            before,
        )

    def test_invalid_url_is_atomic(self):
        self._seed('STRIPE_SECRET_KEY', 'old-secret', 'stripe')
        self._seed('STRIPE_CUSTOMER_PORTAL_URL', 'https://old.example', 'stripe')
        self._assert_unchanged_after_invalid(
            'stripe',
            {
                'STRIPE_SECRET_KEY': 'changed-secret',
                'STRIPE_CUSTOMER_PORTAL_URL': 'not-a-url',
            },
            'STRIPE_CUSTOMER_PORTAL_URL', 'URL', 'payments',
        )

    def test_invalid_integer_is_atomic(self):
        self._seed('EMAIL_BATCH_SIZE', '200', 'ses')
        self._seed('SES_CONFIGURATION_SET_NAME', 'old-set', 'ses')
        self._assert_unchanged_after_invalid(
            'ses',
            {
                'EMAIL_BATCH_SIZE': '2.5',
                'SES_CONFIGURATION_SET_NAME': 'changed-set',
            },
            'EMAIL_BATCH_SIZE', 'integer', 'messaging',
        )

    def test_invalid_boolean_is_atomic(self):
        self._seed('SLACK_ENABLED', 'true', 'slack')
        self._seed('SLACK_BOT_TOKEN', 'old-token', 'slack')
        self._assert_unchanged_after_invalid(
            'slack',
            {'SLACK_ENABLED': 'yes', 'SLACK_BOT_TOKEN': 'changed-token'},
            'SLACK_ENABLED', 'boolean', 'messaging',
        )

    def test_invalid_email_is_atomic(self):
        self._seed('SES_WELCOME_REPLY_TO_EMAIL', 'old@example.com', 'ses')
        self._seed('SES_CONFIGURATION_SET_NAME', 'old-set', 'ses')
        self._assert_unchanged_after_invalid(
            'ses',
            {
                'SES_WELCOME_REPLY_TO_EMAIL': 'bad-address',
                'SES_CONFIGURATION_SET_NAME': 'changed-set',
            },
            'SES_WELCOME_REPLY_TO_EMAIL', 'email address', 'messaging',
        )

    @patch('studio.views.settings.clear_config_cache')
    def test_valid_values_count_clears_and_boolean_override(self, clear_cache):
        self._seed('SLACK_ENABLED', 'true', 'slack')
        self._seed('SLACK_BOT_TOKEN', 'old-token', 'slack')
        response = self.client.post(
            '/studio/settings/slack/save/',
            {
                'clear_override': 'SLACK_ENABLED',
                'SLACK_BOT_TOKEN': 'old-token',
            },
            follow=True,
        )
        # The second Slack checkbox is unchecked, so the browser-equivalent
        # submission persists its explicit false value as well.
        self.assertContains(response, 'Saved 2 settings in Slack.')
        self.assertContains(
            response,
            'Cleared override for SLACK_ENABLED — now using env/default.',
        )
        self.assertFalse(
            IntegrationSetting.objects.filter(key='SLACK_ENABLED').exists()
        )
        self.assertEqual(
            IntegrationSetting.objects.get(key='SLACK_BOT_TOKEN').value,
            'old-token',
        )
        clear_cache.assert_called_once()

    def test_empty_without_override_has_no_false_clear_message(self):
        response = self.client.post(
            '/studio/settings/stripe/save/',
            {'STRIPE_CUSTOMER_PORTAL_URL': 'https://portal.example.com'},
            follow=True,
        )
        messages = [str(message) for message in response.context['messages']]
        self.assertFalse(any('Cleared override' in message for message in messages))
        self.assertEqual(
            IntegrationSetting.objects.get(key='STRIPE_CUSTOMER_PORTAL_URL').value,
            'https://portal.example.com',
        )

    def test_unchecked_boolean_stores_explicit_false(self):
        self.client.post('/studio/settings/slack/save/', {'SLACK_BOT_TOKEN': 'x'})
        self.assertEqual(
            IntegrationSetting.objects.get(key='SLACK_ENABLED').value,
            'false',
        )
