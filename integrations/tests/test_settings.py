"""Tests for integration settings: model, config helper, and studio views."""

import os
from unittest.mock import patch

from allauth.socialaccount.models import SocialApp
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import OperationalError
from django.test import TestCase

from email_app.services.email_classification import (
    EMAIL_KIND_PROMOTIONAL,
    EMAIL_KIND_TRANSACTIONAL,
    get_sender_for_kind,
)
from integrations.config import clear_config_cache, get_config
from integrations.models import IntegrationSetting
from integrations.settings_registry import INTEGRATION_GROUPS

User = get_user_model()


class GetConfigTest(TestCase):
    """Tests for the get_config() helper."""

    def setUp(self):
        # Clear cache before each test to avoid cross-test pollution
        clear_config_cache()

    def tearDown(self):
        clear_config_cache()

    def test_returns_db_value_over_env_var(self):
        IntegrationSetting.objects.create(
            key='TEST_KEY', value='from_db', group='test',
        )
        with patch.dict(os.environ, {'TEST_KEY': 'from_env'}):
            result = get_config('TEST_KEY')
        self.assertEqual(result, 'from_db')

    def test_falls_back_to_env_when_db_empty(self):
        with patch.dict(os.environ, {'TEST_KEY': 'from_env'}):
            result = get_config('TEST_KEY')
        self.assertEqual(result, 'from_env')

    def test_falls_back_to_default_when_nothing_set(self):
        result = get_config('NONEXISTENT_KEY', 'my_default')
        self.assertEqual(result, 'my_default')

    def test_empty_db_value_falls_back_to_env(self):
        IntegrationSetting.objects.create(
            key='TEST_KEY', value='', group='test',
        )
        with patch.dict(os.environ, {'TEST_KEY': 'from_env'}):
            result = get_config('TEST_KEY')
        self.assertEqual(result, 'from_env')

    def test_clear_cache_causes_reload(self):
        with patch.dict(os.environ, {'TEST_KEY': 'env_val'}):
            result1 = get_config('TEST_KEY')
            self.assertEqual(result1, 'env_val')

        # Add DB value and clear cache
        IntegrationSetting.objects.create(
            key='TEST_KEY', value='db_val', group='test',
        )
        clear_config_cache()
        result2 = get_config('TEST_KEY')
        self.assertEqual(result2, 'db_val')

    def test_worker_uncached_falls_back_to_env_when_db_unavailable(self):
        with (
            patch.dict(
                os.environ,
                {
                    'DJANGO_QCLUSTER_PROCESS': 'true',
                    'WORKER_DB_DOWN_KEY': 'from_env',
                },
            ),
            patch.object(
                IntegrationSetting.objects,
                'filter',
                side_effect=OperationalError('DB unreachable'),
            ),
        ):
            with self.assertLogs('integrations.config', level='WARNING'):
                result = get_config('WORKER_DB_DOWN_KEY', 'fallback')

        self.assertEqual(result, 'from_env')

    def test_worker_uncached_falls_back_to_default_when_db_unavailable(self):
        with (
            patch.dict(os.environ, {'DJANGO_QCLUSTER_PROCESS': 'true'}),
            patch.object(
                IntegrationSetting.objects,
                'filter',
                side_effect=OperationalError('DB unreachable'),
            ),
        ):
            os.environ.pop('WORKER_DB_DOWN_DEFAULT_KEY', None)
            with self.assertLogs('integrations.config', level='WARNING'):
                result = get_config('WORKER_DB_DOWN_DEFAULT_KEY', 'fallback')

        self.assertEqual(result, 'fallback')

    def test_worker_uncached_does_not_swallow_programmer_errors(self):
        with (
            patch.dict(os.environ, {'DJANGO_QCLUSTER_PROCESS': 'true'}),
            patch.object(
                IntegrationSetting.objects,
                'filter',
                side_effect=TypeError('programmer bug'),
            ),
        ):
            with self.assertRaises(TypeError):
                get_config('WORKER_PROGRAMMER_BUG_KEY', 'fallback')


class SesSenderConfigTest(TestCase):
    """Separate transactional/promotional sender keys resolve correctly."""

    def setUp(self):
        clear_config_cache()

    def tearDown(self):
        clear_config_cache()

    def test_default_transactional_sender(self):
        self.assertEqual(
            settings.SES_TRANSACTIONAL_FROM_EMAIL,
            'noreply@aishippinglabs.com',
        )

    def test_default_promotional_sender(self):
        self.assertEqual(
            settings.SES_PROMOTIONAL_FROM_EMAIL,
            'content@aishippinglabs.com',
        )

    def test_studio_override_wins_for_transactional_sender(self):
        IntegrationSetting.objects.create(
            key='SES_TRANSACTIONAL_FROM_EMAIL',
            value='tx@example.test',
            group='ses',
        )
        clear_config_cache()

        self.assertEqual(
            get_sender_for_kind(EMAIL_KIND_TRANSACTIONAL),
            'tx@example.test',
        )

    def test_studio_override_wins_for_promotional_sender(self):
        IntegrationSetting.objects.create(
            key='SES_PROMOTIONAL_FROM_EMAIL',
            value='promo@example.test',
            group='ses',
        )
        clear_config_cache()

        self.assertEqual(
            get_sender_for_kind(EMAIL_KIND_PROMOTIONAL),
            'promo@example.test',
        )


class SettingsDashboardViewTest(TestCase):
    """Tests for the Studio settings dashboard view."""

    @classmethod
    def setUpTestData(cls):
        cls.staff_user = User.objects.create_user(
            email='admin@test.com', password='testpass', is_staff=True,
        )
        cls.regular_user = User.objects.create_user(
            email='user@test.com', password='testpass', is_staff=False,
        )

    def test_requires_staff(self):
        response = self.client.get('/studio/settings/')
        # Should redirect to login
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)

    def test_non_staff_forbidden(self):
        self.client.login(email='user@test.com', password='testpass')
        response = self.client.get('/studio/settings/')
        self.assertEqual(response.status_code, 403)

    def test_staff_sees_dashboard(self):
        self.client.login(email='admin@test.com', password='testpass')
        response = self.client.get('/studio/settings/')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'studio/settings/dashboard.html')

    def test_dashboard_shows_all_groups(self):
        self.client.login(email='admin@test.com', password='testpass')
        response = self.client.get('/studio/settings/')
        groups = response.context['groups']
        group_names = [g['name'] for g in groups]
        self.assertIn('stripe', group_names)
        self.assertIn('zoom', group_names)
        self.assertIn('github', group_names)
        self.assertIn('slack', group_names)

    def test_dashboard_shows_status_not_configured(self):
        self.client.login(email='admin@test.com', password='testpass')
        zoom_keys = ['ZOOM_CLIENT_ID', 'ZOOM_CLIENT_SECRET', 'ZOOM_ACCOUNT_ID', 'ZOOM_WEBHOOK_SECRET_TOKEN']
        env_override = {k: '' for k in zoom_keys}
        with patch.dict(os.environ, env_override, clear=False):
            # Remove the keys entirely if they exist
            for k in zoom_keys:
                os.environ.pop(k, None)
            response = self.client.get('/studio/settings/')
        groups = response.context['groups']
        zoom_group = next(g for g in groups if g['name'] == 'zoom')
        self.assertEqual(zoom_group['status'], 'not_configured')

    def test_dashboard_shows_status_configured(self):
        for key in ['ZOOM_CLIENT_ID', 'ZOOM_CLIENT_SECRET', 'ZOOM_ACCOUNT_ID', 'ZOOM_WEBHOOK_SECRET_TOKEN']:
            IntegrationSetting.objects.create(key=key, value='val', group='zoom')
        self.client.login(email='admin@test.com', password='testpass')
        response = self.client.get('/studio/settings/')
        groups = response.context['groups']
        zoom_group = next(g for g in groups if g['name'] == 'zoom')
        self.assertEqual(zoom_group['status'], 'configured')

    def test_dashboard_shows_status_partial(self):
        IntegrationSetting.objects.create(key='ZOOM_CLIENT_ID', value='val', group='zoom')
        self.client.login(email='admin@test.com', password='testpass')
        zoom_keys = ['ZOOM_CLIENT_SECRET', 'ZOOM_ACCOUNT_ID', 'ZOOM_WEBHOOK_SECRET_TOKEN']
        with patch.dict(os.environ, {k: '' for k in zoom_keys}, clear=False):
            for k in zoom_keys:
                os.environ.pop(k, None)
            response = self.client.get('/studio/settings/')
        groups = response.context['groups']
        zoom_group = next(g for g in groups if g['name'] == 'zoom')
        self.assertEqual(zoom_group['status'], 'partial')

    def test_secret_fields_marked_is_secret(self):
        self.client.login(email='admin@test.com', password='testpass')
        response = self.client.get('/studio/settings/')
        groups = response.context['groups']
        zoom_group = next(g for g in groups if g['name'] == 'zoom')
        client_id_field = next(f for f in zoom_group['fields'] if f['key'] == 'ZOOM_CLIENT_ID')
        self.assertTrue(client_id_field['is_secret'])

    def test_env_source_shown_when_value_from_env(self):
        self.client.login(email='admin@test.com', password='testpass')
        with patch.dict(os.environ, {'ZOOM_CLIENT_ID': 'env_val'}):
            response = self.client.get('/studio/settings/')
        groups = response.context['groups']
        zoom_group = next(g for g in groups if g['name'] == 'zoom')
        client_id_field = next(f for f in zoom_group['fields'] if f['key'] == 'ZOOM_CLIENT_ID')
        self.assertEqual(client_id_field['source'], 'env')

    def test_pem_field_marked_multiline(self):
        self.client.login(email='admin@test.com', password='testpass')
        response = self.client.get('/studio/settings/')
        groups = response.context['groups']
        github_group = next(g for g in groups if g['name'] == 'github')
        pem_field = next(f for f in github_group['fields'] if f['key'] == 'GITHUB_APP_PRIVATE_KEY')
        self.assertTrue(pem_field['multiline'])

    def test_github_default_secret_path_counts_as_configured(self):
        IntegrationSetting.objects.create(
            key='GITHUB_APP_ID',
            value='3143490',
            group='github',
        )
        IntegrationSetting.objects.create(
            key='GITHUB_APP_INSTALLATION_ID',
            value='117839867',
            group='github',
        )
        self.client.login(email='admin@test.com', password='testpass')
        response = self.client.get('/studio/settings/')
        groups = response.context['groups']
        github_group = next(g for g in groups if g['name'] == 'github')
        secret_path_field = next(
            f for f in github_group['fields']
            if f['key'] == 'GITHUB_APP_PRIVATE_KEY_SECRET_ID'
        )

        self.assertEqual(github_group['status'], 'configured')
        self.assertEqual(secret_path_field['source'], 'default')
        self.assertEqual(
            secret_path_field['current_value'],
            'ai-shipping-labs/github-app-private-key',
        )

    def test_dashboard_groups_settings_into_navigation_sections(self):
        self.client.login(email='admin@test.com', password='testpass')
        response = self.client.get('/studio/settings/')

        sections = response.context['settings_sections']
        section_ids = [section['id'] for section in sections]
        self.assertEqual(
            section_ids,
            ['auth', 'payments', 'content', 'messaging', 'storage', 'site'],
        )

        groups_by_section = {
            section['id']: [group['name'] for group in section['groups']]
            for section in sections
        }
        self.assertEqual(groups_by_section['payments'], ['stripe'])
        self.assertEqual(groups_by_section['content'], ['zoom', 'youtube', 'github'])
        self.assertEqual(groups_by_section['messaging'], ['ses', 'slack'])
        self.assertEqual(groups_by_section['storage'], ['s3_recordings', 's3_content'])
        self.assertEqual(groups_by_section['site'], ['site'])

        assigned_group_names = [
            group['name']
            for section in sections
            for group in section['groups']
        ]
        self.assertCountEqual(
            assigned_group_names,
            [group_def['name'] for group_def in INTEGRATION_GROUPS],
        )

        body = response.content.decode()
        self.assertIn('data-testid="settings-status-summary"', body)
        self.assertIn('data-testid="settings-section-nav"', body)
        self.assertIn('href="#payments"', body)
        self.assertIn('id="payments"', body)

    def test_dashboard_status_summary_counts_sources_and_risk_groups(self):
        SocialApp.objects.create(
            provider='google',
            name='Google',
            client_id='google-client',
            secret='google-secret',
        )
        IntegrationSetting.objects.create(
            key='STRIPE_SECRET_KEY',
            value='sk_test',
            is_secret=True,
            group='stripe',
        )

        self.client.login(email='admin@test.com', password='testpass')
        with patch.dict(
            os.environ,
            {'SES_TRANSACTIONAL_FROM_EMAIL': 'ops@example.test'},
            clear=True,
        ):
            response = self.client.get('/studio/settings/')

        summary = response.context['status_summary']
        expected_total_items = len(response.context['auth_providers']) + len(response.context['groups'])
        self.assertEqual(summary['total_items'], expected_total_items)
        self.assertEqual(summary['configured_count'], 1)
        # Stripe has one DB-backed key, SES has one env-backed key, and
        # GitHub has the default Secrets Manager path but no App IDs.
        self.assertEqual(summary['partial_count'], 3)
        self.assertEqual(
            summary['missing_count'],
            expected_total_items - summary['configured_count'] - summary['partial_count'],
        )
        self.assertEqual(summary['db_override_count'], 1)
        self.assertEqual(summary['env_backed_count'], 1)
        self.assertGreater(summary['missing_required_values'], 0)
        self.assertIn(
            {'label': 'Stripe', 'section_label': 'Payments', 'status': 'partial'},
            summary['high_risk_items'],
        )
        self.assertIn(
            {'label': 'Google OAuth', 'section_label': 'Auth', 'status': 'configured'},
            summary['high_risk_items'],
        )

    def test_uncategorized_registry_group_appears_in_other_section(self):
        self.client.login(email='admin@test.com', password='testpass')
        registry = [
            *INTEGRATION_GROUPS,
            {
                'name': 'mystery',
                'label': 'Mystery Service',
                'keys': [
                    {
                        'key': 'MYSTERY_TOKEN',
                        'is_secret': True,
                        'description': 'Token for an unmapped integration.',
                    },
                ],
            },
        ]

        with patch('studio.views.settings.INTEGRATION_GROUPS', registry):
            response = self.client.get('/studio/settings/')

        sections = response.context['settings_sections']
        other_section = next(section for section in sections if section['id'] == 'other')
        self.assertEqual([group['name'] for group in other_section['groups']], ['mystery'])
        body = response.content.decode()
        self.assertIn('href="#other"', body)
        self.assertIn('Mystery Service', body)


class SettingsSaveGroupViewTest(TestCase):
    """Tests for saving integration settings per group."""

    @classmethod
    def setUpTestData(cls):
        cls.staff_user = User.objects.create_user(
            email='admin@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        clear_config_cache()

    def tearDown(self):
        clear_config_cache()

    def test_save_creates_settings(self):
        self.client.login(email='admin@test.com', password='testpass')
        response = self.client.post('/studio/settings/zoom/save/', {
            'ZOOM_CLIENT_ID': 'my_client_id',
            'ZOOM_CLIENT_SECRET': 'my_secret',
            'ZOOM_ACCOUNT_ID': 'my_account',
            'ZOOM_WEBHOOK_SECRET_TOKEN': 'my_token',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            IntegrationSetting.objects.get(key='ZOOM_CLIENT_ID').value,
            'my_client_id',
        )
        self.assertEqual(
            IntegrationSetting.objects.get(key='ZOOM_CLIENT_SECRET').value,
            'my_secret',
        )

    def test_save_updates_existing_settings(self):
        IntegrationSetting.objects.create(
            key='ZOOM_CLIENT_ID', value='old_val', group='zoom',
        )
        self.client.login(email='admin@test.com', password='testpass')
        self.client.post('/studio/settings/zoom/save/', {
            'ZOOM_CLIENT_ID': 'new_val',
            'ZOOM_CLIENT_SECRET': '',
            'ZOOM_ACCOUNT_ID': '',
            'ZOOM_WEBHOOK_SECRET_TOKEN': '',
        })
        setting = IntegrationSetting.objects.get(key='ZOOM_CLIENT_ID')
        self.assertEqual(setting.value, 'new_val')

    def test_save_clears_config_cache(self):
        # Populate cache
        get_config('ZOOM_CLIENT_ID', 'default')

        self.client.login(email='admin@test.com', password='testpass')
        self.client.post('/studio/settings/zoom/save/', {
            'ZOOM_CLIENT_ID': 'new_val',
            'ZOOM_CLIENT_SECRET': '',
            'ZOOM_ACCOUNT_ID': '',
            'ZOOM_WEBHOOK_SECRET_TOKEN': '',
        })
        # After save, cache should be cleared and new value returned
        result = get_config('ZOOM_CLIENT_ID')
        self.assertEqual(result, 'new_val')

    def test_save_unknown_group_returns_error(self):
        self.client.login(email='admin@test.com', password='testpass')
        response = self.client.post('/studio/settings/nonexistent/save/', {})
        self.assertEqual(response.status_code, 302)

    def test_save_requires_staff(self):
        response = self.client.post('/studio/settings/zoom/save/', {
            'ZOOM_CLIENT_ID': 'should_not_save',
        })
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)
        self.assertEqual(IntegrationSetting.objects.count(), 0)

    def test_save_sets_group_and_metadata(self):
        self.client.login(email='admin@test.com', password='testpass')
        self.client.post('/studio/settings/zoom/save/', {
            'ZOOM_CLIENT_ID': 'val',
            'ZOOM_CLIENT_SECRET': 'val',
            'ZOOM_ACCOUNT_ID': 'val',
            'ZOOM_WEBHOOK_SECRET_TOKEN': 'val',
        })
        setting = IntegrationSetting.objects.get(key='ZOOM_CLIENT_ID')
        self.assertEqual(setting.group, 'zoom')
        self.assertTrue(setting.is_secret)
        self.assertIn('client ID', setting.description)


class SettingsDashboardAutofillSuppressionTest(TestCase):
    """Regression net for autofill-suppression attributes on settings forms."""

    @classmethod
    def setUpTestData(cls):
        cls.staff_user = User.objects.create_user(
            email='admin@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='admin@test.com', password='testpass')

    def test_settings_page_includes_autocomplete_off(self):
        # Browser password managers treat <input type="text"> next to
        # <input type="password"> as a sign-in form unless the inputs
        # carry autocomplete="off".
        response = self.client.get('/studio/settings/')
        self.assertContains(response, 'autocomplete="off"')

    def test_settings_page_includes_extension_optout_attrs(self):
        # 1Password / Bitwarden / LastPass respect data-1p-ignore /
        # data-bwignore / data-lpignore even when they ignore the HTML
        # standard autocomplete attribute.
        response = self.client.get('/studio/settings/')
        self.assertContains(response, 'data-1p-ignore')
