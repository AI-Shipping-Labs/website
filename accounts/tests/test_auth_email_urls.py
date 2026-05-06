"""Auth helpers must resolve ``SITE_BASE_URL`` through the DB-aware
helper so a Studio override changes verification + password-reset
URLs (issue #435).

``EmailService`` sends via SES directly (``_send_ses``), so these tests
patch that method and inspect the rendered HTML body that would have
been sent — that's where the verify/reset URL actually appears.
"""

import json
from unittest.mock import patch

from django.test import TestCase, override_settings

from accounts.models import User
from email_app.services.email_service import EmailService
from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting


def _set_override(value):
    IntegrationSetting.objects.create(
        key='SITE_BASE_URL', value=value, group='site',
    )
    clear_config_cache()


class _AuthEmailUrlBase(TestCase):
    def setUp(self):
        clear_config_cache()

    def tearDown(self):
        clear_config_cache()

    def _captured_email_html(self, mock_ses):
        # _send_ses(to_email, subject, html_body, ...) — html_body is
        # the third positional arg.
        self.assertEqual(mock_ses.call_count, 1, 'Expected exactly one SES send')
        return mock_ses.call_args[0][2]


@override_settings(SITE_BASE_URL='https://env.example.com')
class VerificationEmailUrlTest(_AuthEmailUrlBase):
    """Registration sends a verification email whose URL respects the
    override."""

    @patch.object(EmailService, '_send_ses', return_value='ses-1')
    def test_verification_email_uses_db_override_when_set(self, mock_ses):
        _set_override('https://override.example.com')
        resp = self.client.post(
            '/api/register',
            data=json.dumps({
                'email': 'verify-override@example.com',
                'password': 'secure1234',
            }),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 201)
        html = self._captured_email_html(mock_ses)
        self.assertIn(
            'https://override.example.com/api/verify-email?token=',
            html,
        )
        self.assertNotIn('https://env.example.com/api/verify-email', html)

    @patch.object(EmailService, '_send_ses', return_value='ses-2')
    def test_verification_email_uses_settings_when_no_override(self, mock_ses):
        # Regression guard: with no DB row, the env value is used.
        self.assertFalse(
            IntegrationSetting.objects.filter(key='SITE_BASE_URL').exists()
        )
        resp = self.client.post(
            '/api/register',
            data=json.dumps({
                'email': 'verify-env@example.com',
                'password': 'secure1234',
            }),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 201)
        html = self._captured_email_html(mock_ses)
        self.assertIn(
            'https://env.example.com/api/verify-email?token=',
            html,
        )


@override_settings(SITE_BASE_URL='https://env.example.com')
class PasswordResetEmailUrlTest(_AuthEmailUrlBase):
    """Password-reset email URL also follows the override."""

    @patch.object(EmailService, '_send_ses', return_value='ses-3')
    def test_password_reset_email_uses_db_override_when_set(self, mock_ses):
        User.objects.create_user(
            email='reset-override@example.com', password='secure1234',
        )
        _set_override('https://override.example.com')

        resp = self.client.post(
            '/api/password-reset-request',
            data=json.dumps({'email': 'reset-override@example.com'}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)
        html = self._captured_email_html(mock_ses)
        self.assertIn(
            'https://override.example.com/api/password-reset?token=',
            html,
        )
        self.assertNotIn('https://env.example.com/api/password-reset', html)

    @patch.object(EmailService, '_send_ses', return_value='ses-4')
    def test_password_reset_email_uses_settings_when_no_override(self, mock_ses):
        User.objects.create_user(
            email='reset-env@example.com', password='secure1234',
        )
        resp = self.client.post(
            '/api/password-reset-request',
            data=json.dumps({'email': 'reset-env@example.com'}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)
        html = self._captured_email_html(mock_ses)
        self.assertIn(
            'https://env.example.com/api/password-reset?token=',
            html,
        )
