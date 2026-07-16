"""Studio, registry, and API coverage for issue #1268 runtime settings."""

import json
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import Token
from email_app.tasks.send_campaign import _get_batch_size
from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting
from integrations.services.llm.backends import _resolve_max_retries
from integrations.settings_registry import get_group_by_name
from payments.stripe_links import get_stripe_payment_links

User = get_user_model()

LINKS = {
    tier: {
        period: f'https://runtime.test/{tier}/{period}'
        for period in ('monthly', 'annual')
    }
    for tier in ('basic', 'main', 'premium')
}
KEYS = {
    'stripe': 'STRIPE_PAYMENT_LINKS',
    'ses': 'EMAIL_BATCH_SIZE',
    'llm': 'LLM_MAX_RETRIES',
}


class RuntimeSettingsRegistryTest(TestCase):
    def test_registry_metadata_and_documentation_anchors(self):
        expectations = {
            'stripe': ('STRIPE_PAYMENT_LINKS', 'stripe.md', True, None),
            'ses': ('EMAIL_BATCH_SIZE', 'ses.md', False, '200'),
            'llm': ('LLM_MAX_RETRIES', 'llm.md', False, '6'),
        }
        for group_name, (key, filename, multiline, default) in expectations.items():
            with self.subTest(key=key):
                entries = {
                    item['key']: item
                    for item in get_group_by_name(group_name)['keys']
                }
                entry = entries[key]
                self.assertFalse(entry['is_secret'])
                self.assertTrue(entry['optional'])
                self.assertTrue(entry['description'])
                self.assertEqual(entry.get('multiline', False), multiline)
                if default is not None:
                    self.assertEqual(entry['default'], default)
                self.assertEqual(
                    entry['docs_url'],
                    f'_docs/integrations/{filename}#{key.lower()}',
                )
                docs = (
                    Path(settings.BASE_DIR) / '_docs' / 'integrations' / filename
                ).read_text(encoding='utf-8')
                self.assertIn(f'## {key}\n', docs)


class RuntimeSettingsStudioTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='runtime-settings-staff@test.com',
            password='testpass',
            is_staff=True,
        )

    def setUp(self):
        clear_config_cache()
        self.client.login(
            email='runtime-settings-staff@test.com', password='testpass',
        )

    def tearDown(self):
        IntegrationSetting.objects.filter(key__in=KEYS.values()).delete()
        clear_config_cache()

    def test_fields_render_with_controls_docs_and_source_badges(self):
        IntegrationSetting.objects.create(
            key='STRIPE_PAYMENT_LINKS',
            value=json.dumps(LINKS),
            group='stripe',
        )
        response = self.client.get('/studio/settings/')

        self.assertContains(response, 'textarea id="field-STRIPE_PAYMENT_LINKS"')
        for key in KEYS.values():
            self.assertContains(response, f'data-field-key="{key}"')
            self.assertContains(response, f'data-docs-link="{key}"')
        stripe_group = next(
            group for group in response.context['groups']
            if group['name'] == 'stripe'
        )
        stripe_field = next(
            field for field in stripe_group['fields']
            if field['key'] == 'STRIPE_PAYMENT_LINKS'
        )
        self.assertEqual(stripe_field['source'], 'db')
        self.assertContains(
            response,
            'Clearing this override restores the Django settings fallback.',
        )

        IntegrationSetting.objects.filter(key='STRIPE_PAYMENT_LINKS').delete()
        clear_config_cache()
        response = self.client.get('/studio/settings/')
        stripe_group = next(
            group for group in response.context['groups']
            if group['name'] == 'stripe'
        )
        stripe_field = next(
            field for field in stripe_group['fields']
            if field['key'] == 'STRIPE_PAYMENT_LINKS'
        )
        self.assertEqual(stripe_field['source'], 'django_settings')
        self.assertContains(response, 'data-source-badge="django_settings"')

    def test_studio_save_and_clear_refresh_runtime_values(self):
        cases = [
            ('stripe', 'STRIPE_PAYMENT_LINKS', json.dumps(LINKS)),
            ('ses', 'EMAIL_BATCH_SIZE', '17'),
            ('llm', 'LLM_MAX_RETRIES', '2'),
        ]
        for group, key, value in cases:
            with self.subTest(key=key):
                self.client.post(f'/studio/settings/{group}/save/', {key: value})
                self.assertEqual(
                    IntegrationSetting.objects.get(key=key).value, value,
                )
                if key == 'STRIPE_PAYMENT_LINKS':
                    self.assertEqual(get_stripe_payment_links(), LINKS)
                elif key == 'EMAIL_BATCH_SIZE':
                    self.assertEqual(_get_batch_size(), 17)
                else:
                    self.assertEqual(_resolve_max_retries(), 2)

                self.client.post(f'/studio/settings/{group}/save/', {key: ''})
                self.assertFalse(
                    IntegrationSetting.objects.filter(key=key).exists(),
                )


class RuntimeSettingsApiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='runtime-settings-api@test.com', is_staff=True,
        )
        cls.token = Token.objects.create(user=cls.staff, name='runtime-settings')

    def setUp(self):
        clear_config_cache()
        self.auth = {'HTTP_AUTHORIZATION': f'Token {self.token.key}'}

    def tearDown(self):
        IntegrationSetting.objects.filter(key__in=KEYS.values()).delete()
        clear_config_cache()

    def test_get_lists_metadata_without_values(self):
        IntegrationSetting.objects.create(
            key='STRIPE_PAYMENT_LINKS', value='do-not-leak', group='stripe',
        )
        response = self.client.get('/api/integrations/settings', **self.auth)
        entries = {
            item['key']: item for item in response.json()['settings']
        }

        self.assertEqual(response.status_code, 200)
        for group, key in KEYS.items():
            self.assertEqual(entries[key]['group'], group)
            self.assertTrue(entries[key]['description'])
            self.assertTrue(entries[key]['docs_url'])
            self.assertNotIn('value', entries[key])
        self.assertNotContains(response, 'do-not-leak')

    def test_post_sets_and_clears_all_three_without_echo(self):
        updates = [
            {'key': 'STRIPE_PAYMENT_LINKS', 'value': json.dumps(LINKS)},
            {'key': 'EMAIL_BATCH_SIZE', 'value': '19'},
            {'key': 'LLM_MAX_RETRIES', 'value': '3'},
        ]
        response = self.client.post(
            '/api/integrations/settings',
            data=json.dumps({'updates': updates}),
            content_type='application/json',
            **self.auth,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'ok', 'updated': 3})
        self.assertNotIn('runtime.test', response.content.decode())
        self.assertEqual(get_stripe_payment_links(), LINKS)
        self.assertEqual(_get_batch_size(), 19)
        self.assertEqual(_resolve_max_retries(), 3)

        response = self.client.post(
            '/api/integrations/settings',
            data=json.dumps({'updates': [
                {'key': key, 'value': ''} for key in KEYS.values()
            ]}),
            content_type='application/json',
            **self.auth,
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            IntegrationSetting.objects.filter(key__in=KEYS.values()).exists(),
        )
