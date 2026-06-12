"""Tests for the Calendly OAuth connect / callback (issue #884).

The live round-trip against the real Calendly account is [HUMAN]; these
cover the request handling: staff gating, redirect construction, token
exchange success, and the error branches that must not raise.
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting

User = get_user_model()

CONNECT_URL = '/studio/integrations/calendly/connect'
CALLBACK_URL = '/studio/integrations/calendly/callback'


class _FakeResponse:
    def __init__(self, json_data):
        self._json = json_data

    def raise_for_status(self):
        return None

    def json(self):
        return self._json


@tag('core')
class CalendlyConnectTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.member = User.objects.create_user(email='member@test.com', password='pw')

    def tearDown(self):
        clear_config_cache()

    def test_connect_requires_staff(self):
        self.client.login(email='member@test.com', password='pw')
        resp = self.client.get(CONNECT_URL)
        # Authenticated non-staff get a 403, never the Calendly redirect.
        self.assertEqual(resp.status_code, 403)

    def test_connect_without_client_id_redirects_to_settings(self):
        self.client.login(email='staff@test.com', password='pw')
        resp = self.client.get(CONNECT_URL)
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/studio/settings', resp['Location'])

    def test_connect_with_client_id_redirects_to_calendly_authorize(self):
        IntegrationSetting.objects.create(
            key='CALENDLY_OAUTH_CLIENT_ID', value='cid-123', group='calendly',
        )
        clear_config_cache()
        self.client.login(email='staff@test.com', password='pw')
        resp = self.client.get(CONNECT_URL)
        self.assertEqual(resp.status_code, 302)
        self.assertIn('auth.calendly.com/oauth/authorize', resp['Location'])
        self.assertIn('client_id=cid-123', resp['Location'])
        # The redirect_uri is URL-encoded in the query string.
        self.assertIn('calendly%2Fcallback', resp['Location'])


@tag('core')
class CalendlyCallbackTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')
        IntegrationSetting.objects.create(
            key='CALENDLY_OAUTH_CLIENT_ID', value='cid-123', group='calendly',
        )
        IntegrationSetting.objects.create(
            key='CALENDLY_OAUTH_CLIENT_SECRET', value='secret-xyz', group='calendly',
        )
        clear_config_cache()

    def tearDown(self):
        clear_config_cache()

    def test_callback_stores_access_token_on_success(self):
        with patch(
            'studio.views.calendly_oauth.requests.post',
            return_value=_FakeResponse({'access_token': 'host-token-abc'}),
        ):
            resp = self.client.get(CALLBACK_URL, {'code': 'auth-code-1'})
        self.assertEqual(resp.status_code, 302)
        stored = IntegrationSetting.objects.get(key='CALENDLY_ACCESS_TOKEN')
        self.assertEqual(stored.value, 'host-token-abc')
        self.assertTrue(stored.is_secret)

    def test_callback_with_error_param_does_not_store_token(self):
        resp = self.client.get(CALLBACK_URL, {'error': 'access_denied'})
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(
            IntegrationSetting.objects.filter(key='CALENDLY_ACCESS_TOKEN').exists()
        )

    def test_callback_without_code_does_not_store_token(self):
        resp = self.client.get(CALLBACK_URL)
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(
            IntegrationSetting.objects.filter(key='CALENDLY_ACCESS_TOKEN').exists()
        )

    def test_callback_token_exchange_failure_does_not_raise(self):
        import requests

        with patch(
            'studio.views.calendly_oauth.requests.post',
            side_effect=requests.RequestException('boom'),
        ):
            resp = self.client.get(CALLBACK_URL, {'code': 'auth-code-1'})
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(
            IntegrationSetting.objects.filter(key='CALENDLY_ACCESS_TOKEN').exists()
        )
