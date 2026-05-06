"""Email service templates and unsubscribe URL must use the resolved
``SITE_BASE_URL`` (issue #435).
"""

from django.test import TestCase, override_settings

from accounts.models import User
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

    def test_override_with_trailing_slash_is_stripped(self):
        # rstrip('/') must be preserved so URL building doesn't get
        # double-slashes.
        _set_override('https://override.example.com/')
        ctx = _build_context(self.user)
        self.assertEqual(
            ctx['sign_in_url'], 'https://override.example.com/login/',
        )
