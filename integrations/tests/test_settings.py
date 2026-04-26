"""Tests for integration settings: model, config helper, and studio views."""

import os
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from integrations.config import clear_config_cache, get_config
from integrations.models import IntegrationSetting

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
            'confirm_update': 'on',
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
            'confirm_update': 'on',
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
            'confirm_update': 'on',
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
            'confirm_update': 'on',
        })
        setting = IntegrationSetting.objects.get(key='ZOOM_CLIENT_ID')
        self.assertEqual(setting.group, 'zoom')
        self.assertTrue(setting.is_secret)
        self.assertIn('client ID', setting.description)

    def test_save_without_confirm_update_does_not_write(self):
        # Defense-in-depth against browser password-manager autofill
        # silently overwriting credentials. Server is the source of
        # truth: no `confirm_update=on` means no DB write.
        self.client.login(email='admin@test.com', password='testpass')
        response = self.client.post('/studio/settings/zoom/save/', {
            'ZOOM_CLIENT_ID': 'autofilled_junk',
            'ZOOM_CLIENT_SECRET': 'autofilled_junk',
            'ZOOM_ACCOUNT_ID': 'autofilled_junk',
            'ZOOM_WEBHOOK_SECRET_TOKEN': 'autofilled_junk',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(IntegrationSetting.objects.count(), 0)
        # Flash error tells the operator what to do.
        msgs = [str(m) for m in response.wsgi_request._messages]
        self.assertTrue(any('Apply changes' in m for m in msgs))

    def test_save_with_non_on_confirm_update_does_not_write(self):
        # Only the literal string "on" (HTML checkbox value) unlocks the
        # save path. Anything else — including "true" — is rejected.
        self.client.login(email='admin@test.com', password='testpass')
        IntegrationSetting.objects.create(
            key='ZOOM_CLIENT_ID', value='preserved', group='zoom',
        )
        self.client.post('/studio/settings/zoom/save/', {
            'ZOOM_CLIENT_ID': 'overwritten',
            'ZOOM_CLIENT_SECRET': '',
            'ZOOM_ACCOUNT_ID': '',
            'ZOOM_WEBHOOK_SECRET_TOKEN': '',
            'confirm_update': 'true',
        })
        # Existing value MUST NOT be overwritten by a missing/wrong confirm.
        setting = IntegrationSetting.objects.get(key='ZOOM_CLIENT_ID')
        self.assertEqual(setting.value, 'preserved')


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
