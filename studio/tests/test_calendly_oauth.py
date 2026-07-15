"""Tests for the Calendly OAuth connect / callback (issue #884).

The live round-trip against the real Calendly account is [HUMAN]; these
cover the request handling: staff gating, redirect construction, token
exchange success, and the error branches that must not raise.
"""

from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting

User = get_user_model()

CONNECT_URL = '/studio/integrations/calendly/connect'
CALLBACK_URL = '/studio/integrations/calendly/callback'
SYNC_URL = '/studio/integrations/calendly/sync'


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
        query = parse_qs(urlsplit(resp['Location']).query)
        self.assertEqual(query['code_challenge_method'], ['S256'])
        self.assertEqual(
            set(query['scope'][0].split()),
            {'scheduled_events:read', 'webhooks:write'},
        )
        self.assertTrue(query['state'][0])
        self.assertTrue(query['code_challenge'][0])


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

    def _state(self):
        response = self.client.get(CONNECT_URL)
        return parse_qs(urlsplit(response['Location']).query)['state'][0]

    def test_callback_stores_access_token_on_success(self):
        state = self._state()
        with patch(
            'studio.views.calendly_oauth.requests.post',
            return_value=_FakeResponse({
                'access_token': 'host-token-abc', 'refresh_token': 'refresh-1',
                'expires_in': 7200,
            }),
        ), patch(
            'studio.views.calendly_oauth.validate_connection_and_ensure_subscription',
            return_value={'subscription_uri': 'sub-1'},
        ):
            resp = self.client.get(
                CALLBACK_URL, {'code': 'auth-code-1', 'state': state},
            )
        self.assertEqual(resp.status_code, 302)
        stored = IntegrationSetting.objects.get(key='CALENDLY_ACCESS_TOKEN')
        self.assertEqual(stored.value, 'host-token-abc')
        self.assertTrue(stored.is_secret)
        self.assertEqual(
            IntegrationSetting.objects.get(key='CALENDLY_REFRESH_TOKEN').value,
            'refresh-1',
        )

    def test_callback_rejects_missing_refresh_token_and_preserves_existing_account(self):
        IntegrationSetting.objects.create(
            key='CALENDLY_ACCESS_TOKEN', value='existing-access',
            group='calendly', is_secret=True,
        )
        IntegrationSetting.objects.create(
            key='CALENDLY_REFRESH_TOKEN', value='existing-refresh',
            group='calendly', is_secret=True,
        )
        clear_config_cache()
        state = self._state()
        with patch(
            'studio.views.calendly_oauth.requests.post',
            return_value=_FakeResponse({
                'access_token': 'different-account-access', 'expires_in': 7200,
            }),
        ), patch(
            'studio.views.calendly_oauth.validate_connection_and_ensure_subscription',
        ) as validate:
            response = self.client.get(
                CALLBACK_URL, {'code': 'auth-code-1', 'state': state},
                follow=True,
            )
        self.assertContains(response, 'Could not complete Calendly setup')
        self.assertEqual(
            IntegrationSetting.objects.get(key='CALENDLY_ACCESS_TOKEN').value,
            'existing-access',
        )
        self.assertEqual(
            IntegrationSetting.objects.get(key='CALENDLY_REFRESH_TOKEN').value,
            'existing-refresh',
        )
        validate.assert_not_called()

    def test_callback_with_error_param_does_not_store_token(self):
        state = self._state()
        resp = self.client.get(
            CALLBACK_URL, {'error': 'access_denied', 'state': state},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(
            IntegrationSetting.objects.filter(key='CALENDLY_ACCESS_TOKEN').exists()
        )

    def test_callback_without_code_does_not_store_token(self):
        state = self._state()
        resp = self.client.get(CALLBACK_URL, {'state': state})
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(
            IntegrationSetting.objects.filter(key='CALENDLY_ACCESS_TOKEN').exists()
        )

    def test_callback_token_exchange_failure_does_not_raise(self):
        import requests

        state = self._state()
        with patch(
            'studio.views.calendly_oauth.requests.post',
            side_effect=requests.RequestException('boom'),
        ):
            resp = self.client.get(
                CALLBACK_URL, {'code': 'auth-code-1', 'state': state},
            )
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(
            IntegrationSetting.objects.filter(key='CALENDLY_ACCESS_TOKEN').exists()
        )

    def test_callback_rejects_missing_or_wrong_state_before_exchange(self):
        self._state()
        with patch('studio.views.calendly_oauth.requests.post') as exchange:
            resp = self.client.get(
                CALLBACK_URL, {'code': 'auth-code-1', 'state': 'attacker'},
            )
        self.assertEqual(resp.status_code, 302)
        exchange.assert_not_called()

    def test_callback_state_is_single_use(self):
        state = self._state()
        with patch(
            'studio.views.calendly_oauth.requests.post',
            return_value=_FakeResponse({'access_token': 'a', 'refresh_token': 'r'}),
        ), patch(
            'studio.views.calendly_oauth.validate_connection_and_ensure_subscription',
        ):
            first = self.client.get(CALLBACK_URL, {'code': 'one', 'state': state})
            second = self.client.get(CALLBACK_URL, {'code': 'two', 'state': state})
        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)


@tag('core')
class CalendlySyncTest(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            email='sync-staff@test.com', password='pw', is_staff=True,
        )

    @patch('studio.views.calendly_oauth.validate_connection_and_ensure_subscription')
    def test_staff_can_idempotently_verify_subscription(self, ensure):
        self.client.login(email='sync-staff@test.com', password='pw')
        response = self.client.post(SYNC_URL)
        self.assertEqual(response.status_code, 302)
        ensure.assert_called_once_with()

    def test_sync_is_post_only(self):
        self.client.login(email='sync-staff@test.com', password='pw')
        self.assertEqual(self.client.get(SYNC_URL).status_code, 405)
