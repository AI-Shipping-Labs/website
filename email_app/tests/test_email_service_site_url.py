"""Email service templates and unsubscribe URL must use the resolved
``SITE_BASE_URL`` (issue #435).
"""

import datetime
from urllib.parse import parse_qs, urlparse

import jwt
from django.conf import settings
from django.test import TestCase, override_settings

from accounts.models import User
from accounts.utils.tokens import JWT_ALGORITHM
from email_app.services.email_service import EmailService
from email_app.tasks.welcome_imported import (
    _build_context,
    _build_password_reset_url,
)
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
    """``email_app.tasks.welcome_imported`` URL helpers must respect
    the override and preserve ``rstrip('/')``."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email='imp@example.com', password='secure1234',
        )

    def setUp(self):
        clear_config_cache()

    def tearDown(self):
        clear_config_cache()

    def test_welcome_imported_signin_url_uses_db_override(self):
        _set_override('https://override.example.com')
        ctx = _build_context(self.user)
        self.assertEqual(
            ctx['sign_in_url'], 'https://override.example.com/login/',
        )

    def test_welcome_imported_signin_url_falls_back_to_settings(self):
        ctx = _build_context(self.user)
        self.assertEqual(
            ctx['sign_in_url'], 'https://env.example.com/login/',
        )

    def test_welcome_imported_password_reset_url_uses_db_override(self):
        _set_override('https://override.example.com')
        url = _build_password_reset_url(self.user)
        self.assertTrue(
            url.startswith(
                'https://override.example.com/api/password-reset?token='
            ),
            f'Unexpected reset URL: {url!r}',
        )

    def test_welcome_imported_password_reset_url_falls_back_to_settings(self):
        url = _build_password_reset_url(self.user)
        self.assertTrue(
            url.startswith(
                'https://env.example.com/api/password-reset?token='
            ),
            f'Unexpected reset URL: {url!r}',
        )

    def test_welcome_imported_password_reset_token_uses_one_hour_expiry(self):
        started_at = datetime.datetime.now(datetime.timezone.utc)
        url = _build_password_reset_url(self.user)
        payload = _decode_user_action_token(_extract_token(url))
        expires_at = datetime.datetime.fromtimestamp(
            payload["exp"],
            tz=datetime.timezone.utc,
        )

        self.assertEqual(payload["user_id"], self.user.pk)
        self.assertEqual(payload["action"], "password_reset")
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
        ctx = _build_context(self.user)
        self.assertEqual(
            ctx['sign_in_url'], 'https://override.example.com/login/',
        )
