"""Email service templates and unsubscribe URL must use the resolved
``SITE_BASE_URL`` (issue #435).
"""

import datetime
import re
from urllib.parse import parse_qs, urlparse

import jwt
from django.conf import settings
from django.test import TestCase, override_settings

from accounts.models import User
from accounts.utils.tokens import JWT_ALGORITHM, resolve_password_reset_token
from email_app.services.email_service import EmailService
from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting


def _set_override(value):
    IntegrationSetting.objects.create(
        key='SITE_BASE_URL', value=value, group='site',
    )
    clear_config_cache()


def _extract_token(url):
    return parse_qs(urlparse(url).query)["token"][0]


def _decode_user_action_token(token):
    return jwt.decode(
        token,
        settings.SECRET_KEY,
        algorithms=[JWT_ALGORITHM],
        options={"verify_exp": False},
    )


@override_settings(SITE_BASE_URL='https://env.example.com')
class EmailServiceSiteUrlOverrideTest(TestCase):
    """``EmailService._render_template`` and ``_build_unsubscribe_url``
    must respect the override."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email='svc@example.com', password='secure1234',
        )

    def setUp(self):
        clear_config_cache()

    def tearDown(self):
        clear_config_cache()

    def test_render_template_site_url_uses_db_override(self):
        _set_override('https://override.example.com')
        service = EmailService()
        # The welcome template embeds {{ site_url }}/tutorials/ so the
        # resolved host must appear in the rendered HTML.
        _, body_html = service._render_template(
            'welcome', self.user, {'tier_name': 'Free'},
        )
        self.assertIn(
            'https://override.example.com/tutorials/', body_html,
        )
        self.assertNotIn(
            'https://env.example.com/tutorials/', body_html,
        )

    def test_render_template_site_url_falls_back_to_settings(self):
        service = EmailService()
        _, body_html = service._render_template(
            'welcome', self.user, {'tier_name': 'Free'},
        )
        self.assertIn(
            'https://env.example.com/tutorials/', body_html,
        )

    def test_unsubscribe_url_uses_db_override(self):
        _set_override('https://override.example.com')
        url = EmailService()._build_unsubscribe_url(self.user)
        self.assertTrue(
            url.startswith(
                'https://override.example.com/api/unsubscribe?token='
            ),
            f'Unexpected unsubscribe URL: {url!r}',
        )

    def test_unsubscribe_url_falls_back_to_settings(self):
        url = EmailService()._build_unsubscribe_url(self.user)
        self.assertTrue(
            url.startswith(
                'https://env.example.com/api/unsubscribe?token='
            ),
            f'Unexpected unsubscribe URL: {url!r}',
        )

    def test_unsubscribe_url_token_uses_no_expiry_unsubscribe_action(self):
        url = EmailService()._build_unsubscribe_url(self.user)
        payload = _decode_user_action_token(_extract_token(url))

        self.assertEqual(payload["user_id"], self.user.pk)
        self.assertEqual(payload["action"], "unsubscribe")
        self.assertNotIn("exp", payload)


@override_settings(SITE_BASE_URL='https://env.example.com')
class WelcomeOnboardingCtaTest(TestCase):
    """Free / community welcome emails link to onboarding (issue #871).

    Mirrors the paid-tier welcomes (#838/#847) so every "you joined" email
    points new members at the onboarding form.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email='cta@example.com', password='secure1234',
        )

    def setUp(self):
        clear_config_cache()

    def tearDown(self):
        clear_config_cache()

    def test_welcome_links_to_onboarding(self):
        _, body_html = EmailService()._render_template(
            'welcome', self.user, {'tier_name': 'Free'},
        )
        self.assertIn('https://env.example.com/onboarding/', body_html)

    def test_community_invite_links_to_onboarding(self):
        _, body_html = EmailService()._render_template(
            'community_invite', self.user,
            {'slack_invite_url': 'https://slack.example.com/invite'},
        )
        self.assertIn('https://env.example.com/onboarding/', body_html)


@override_settings(SITE_BASE_URL='https://env.example.com')
class WelcomeImportedSiteUrlOverrideTest(TestCase):
    """The imported-member welcome's links are minted by the worker
    resolver from the resolved ``SITE_BASE_URL`` (A1.2 remainder slice 4)
    and must respect the override, preserving ``rstrip('/')``.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email='imp@example.com', password='secure1234',
        )

    def setUp(self):
        clear_config_cache()

    def tearDown(self):
        clear_config_cache()

    def _send_and_render(self):
        from unittest.mock import patch

        from email_app.tasks.welcome_imported import (
            send_imported_welcome_email,
        )
        from email_app.testing import StubSESClient, deliver_pending_mail

        result = send_imported_welcome_email(self.user.pk)
        self.assertEqual(result['status'], 'sent')

        stub = StubSESClient()
        with patch(
            'community_base.mail.backends.ses_local.configured_client',
            return_value=stub,
        ):
            deliver_pending_mail()
        self.assertEqual(len(stub.calls), 1)
        return (
            stub.calls[0]['Content']['Simple']['Subject']['Data'],
            stub.calls[0]['Content']['Simple']['Body']['Html']['Data'],
        )

    def test_welcome_imported_signin_url_uses_db_override(self):
        _set_override('https://override.example.com')
        _subject, body_html = self._send_and_render()
        self.assertIn(
            'https://override.example.com/login/', body_html,
        )
        self.assertNotIn('https://env.example.com/login/', body_html)

    def test_welcome_imported_signin_url_falls_back_to_settings(self):
        _subject, body_html = self._send_and_render()
        self.assertIn('https://env.example.com/login/', body_html)

    def test_welcome_imported_password_reset_url_uses_db_override(self):
        _set_override('https://override.example.com')
        _subject, body_html = self._send_and_render()
        self.assertIn(
            'https://override.example.com/api/password-reset?token=',
            body_html,
        )

    def test_welcome_imported_password_reset_url_falls_back_to_settings(self):
        _subject, body_html = self._send_and_render()
        self.assertIn(
            'https://env.example.com/api/password-reset?token=',
            body_html,
        )

    def test_welcome_imported_password_reset_token_uses_one_hour_expiry(self):

        import jwt as pyjwt

        from accounts.utils.tokens import (
            JWT_ALGORITHM,
        )

        started_at = datetime.datetime.now(datetime.timezone.utc)
        _subject, body_html = self._send_and_render()
        match = re.search(r'/api/password-reset\?token=([A-Za-z0-9_\-.]+)',
                          body_html)
        self.assertIsNotNone(match)
        token = match.group(1)
        payload = pyjwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[JWT_ALGORITHM],
            options={"verify_exp": False},
        )
        expires_at = datetime.datetime.fromtimestamp(
            payload["exp"],
            tz=datetime.timezone.utc,
        )

        self.assertEqual(payload["user_id"], self.user.pk)
        self.assertEqual(payload["action"], "password_reset")
        resolved_user, _validated = resolve_password_reset_token(token)
        self.assertEqual(resolved_user.pk, self.user.pk)
        self.assertGreater(
            expires_at,
            started_at + datetime.timedelta(minutes=59),
        )
        self.assertLess(
            expires_at,
            started_at + datetime.timedelta(hours=1, minutes=1),
        )

    def test_override_with_trailing_slash_is_stripped(self):
        # rstrip('/') must be preserved so URL building doesn't get
        # double-slashes.
        _set_override('https://override.example.com/')
        _subject, body_html = self._send_and_render()
        self.assertIn(
            'https://override.example.com/login/', body_html,
        )
        self.assertNotIn('example.com//', body_html)
