"""Relay draft management and previews never dispatch a real campaign."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import Token
from email_app.testing import StubRelayTemplateClient


class EmailTemplateApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user = get_user_model().objects.create_user(email='template-staff@example.com', is_staff=True)
        cls.token = Token.objects.create(user=user, name='template-test')

    def setUp(self):
        self.relay = StubRelayTemplateClient()
        patcher = patch('api.views.email_templates.configured_template_client', return_value=self.relay)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.url = '/api/email-templates/event_recording_ready'
        self.headers = {'HTTP_AUTHORIZATION': f'Token {self.token.key}'}

    def test_management_requires_staff_token(self):
        self.assertEqual(self.client.get(self.url).status_code, 401)

    def test_save_draft_is_separate_from_publish(self):
        initial = self.client.get(self.url, **self.headers).json()
        response = self.client.put(
            self.url, {'markdown_body': '{{ publish_copy }}'},
            content_type='application/json', **self.headers,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['template']['markdown_body'], '{{ publish_copy }}')
        self.assertEqual(response.json()['versions'], initial['versions'])
        published = self.client.post(f'{self.url}/publish', **self.headers)
        self.assertEqual(published.status_code, 200)
        self.assertEqual(published.json()['version'], 2)

    def test_unknown_draft_field_is_rejected(self):
        response = self.client.put(
            self.url, {'send_now': True}, content_type='application/json', **self.headers,
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()['code'], 'unknown_field')

    def test_preview_uses_explicit_publish_state_without_sending(self):
        response = self.client.post(
            f'{self.url}/preview', {'context': {'publish_copy': 'Already published.'}},
            content_type='application/json', **self.headers,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.relay.calls[-1]['context']['publish_copy'], 'Already published.')
