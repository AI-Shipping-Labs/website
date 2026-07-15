from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings, tag
from django.utils import timezone

from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting
from integrations.services.calendly_api import (
    CalendlyAPIError,
    refresh_access_token,
    store_token_response,
    validate_connection_and_ensure_subscription,
)


class Response:
    def __init__(self, status, data=None):
        self.status_code = status
        self._data = data or {}
        self.content = b'{}' if data is not None else b''

    def json(self):
        return self._data


@tag('core')
class CalendlyAPITest(TestCase):
    def setUp(self):
        for key, value in {
            'CALENDLY_ACCESS_TOKEN': 'old-access',
            'CALENDLY_REFRESH_TOKEN': 'old-refresh',
            'CALENDLY_ACCESS_TOKEN_EXPIRES_AT': (
                timezone.now() - timedelta(minutes=1)
            ).isoformat(),
            'CALENDLY_OAUTH_CLIENT_ID': 'client',
            'CALENDLY_OAUTH_CLIENT_SECRET': 'secret',
        }.items():
            IntegrationSetting.objects.create(
                key=key, value=value, group='calendly', is_secret='TOKEN' in key,
            )
        clear_config_cache()

    def tearDown(self):
        clear_config_cache()

    @patch('integrations.services.calendly_api.requests.post')
    def test_refresh_atomically_rotates_refresh_token_and_expiry(self, post):
        post.return_value = Response(200, {
            'access_token': 'new-access', 'refresh_token': 'new-refresh',
            'expires_in': 7200,
        })
        self.assertEqual(refresh_access_token(), 'new-access')
        self.assertEqual(
            IntegrationSetting.objects.get(key='CALENDLY_REFRESH_TOKEN').value,
            'new-refresh',
        )

    @patch('integrations.services.calendly_api.requests.post')
    def test_refresh_preserves_locked_refresh_token_when_response_omits_it(self, post):
        post.return_value = Response(200, {
            'access_token': 'new-access', 'expires_in': 7200,
        })
        self.assertEqual(refresh_access_token(), 'new-access')
        self.assertEqual(
            IntegrationSetting.objects.get(key='CALENDLY_REFRESH_TOKEN').value,
            'old-refresh',
        )

    def test_initial_oauth_requires_new_refresh_token_without_mutating_tokens(self):
        with self.assertRaisesMessage(CalendlyAPIError, 'omitted refresh_token'):
            store_token_response(
                {'access_token': 'new-account-access', 'expires_in': 7200},
                require_new_refresh_token=True,
            )
        self.assertEqual(
            IntegrationSetting.objects.get(key='CALENDLY_ACCESS_TOKEN').value,
            'old-access',
        )
        self.assertEqual(
            IntegrationSetting.objects.get(key='CALENDLY_REFRESH_TOKEN').value,
            'old-refresh',
        )

    @patch('integrations.services.calendly_api.requests.post')
    def test_refresh_claims_token_row_before_single_use_rotation(self, post):
        post.return_value = Response(200, {
            'access_token': 'next-access', 'refresh_token': 'next-refresh',
            'expires_in': 7200,
        })
        original = IntegrationSetting.objects.select_for_update
        with patch.object(
            IntegrationSetting.objects, 'select_for_update', wraps=original,
        ) as lock:
            refresh_access_token()
        self.assertGreaterEqual(lock.call_count, 1)

    @patch('integrations.services.calendly_api.requests.post')
    def test_invalid_refresh_clears_stale_oauth_tokens(self, post):
        post.return_value = Response(401, {'error': 'invalid_grant'})
        with self.assertRaises(CalendlyAPIError):
            refresh_access_token()
        self.assertFalse(IntegrationSetting.objects.filter(
            key='CALENDLY_ACCESS_TOKEN',
        ).exists())

    @override_settings(SITE_BASE_URL='https://example.test')
    @patch('integrations.services.calendly_api.api_request')
    def test_validate_connection_creates_required_subscription_once(self, request):
        request.side_effect = [
            {'resource': {'uri': 'https://api.calendly.com/users/U1',
                          'current_organization': 'https://api.calendly.com/organizations/O1'}},
            {'collection': []},
            {'resource': {'uri': 'https://api.calendly.com/webhook_subscriptions/S1'}},
        ]
        result = validate_connection_and_ensure_subscription()
        self.assertEqual(result['subscription_uri'], 'https://api.calendly.com/webhook_subscriptions/S1')
        create = request.call_args_list[2]
        self.assertEqual(create.args[:2], ('POST', '/webhook_subscriptions'))
        self.assertEqual(
            set(create.kwargs['json']['events']),
            {'invitee.created', 'invitee.canceled'},
        )

    @override_settings(SITE_BASE_URL='https://example.test')
    @patch('integrations.services.calendly_api.api_request')
    def test_validate_connection_finds_existing_subscription_on_later_page(self, request):
        subscription_uri = 'https://api.calendly.com/webhook_subscriptions/S2'
        request.side_effect = [
            {'resource': {
                'uri': 'https://api.calendly.com/users/U1',
                'current_organization': 'https://api.calendly.com/organizations/O1',
            }},
            {
                'collection': [],
                'pagination': {'next_page_token': 'next-token'},
            },
            {
                'collection': [{
                    'uri': subscription_uri,
                    'callback_url': 'https://example.test/api/webhooks/calendly',
                    'state': 'active',
                    'events': ['invitee.created', 'invitee.canceled'],
                }],
                'pagination': {'next_page_token': None},
            },
        ]

        result = validate_connection_and_ensure_subscription()

        self.assertEqual(result['subscription_uri'], subscription_uri)
        self.assertEqual(request.call_count, 3)
        self.assertEqual(
            request.call_args_list[2].kwargs['params']['page_token'],
            'next-token',
        )
        self.assertFalse(any(call.args[0] == 'POST' for call in request.call_args_list))
