"""Django coverage for the Studio settings section/filter shell."""

import re

from django.contrib.auth import get_user_model
from django.test import TestCase

User = get_user_model()


class SettingsDashboardNavigationShellTest(TestCase):
    """Server-rendered contract for one-section settings navigation."""

    @classmethod
    def setUpTestData(cls):
        cls.staff_user = User.objects.create_user(
            email='admin@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='admin@test.com', password='testpass')

    def test_dashboard_renders_filter_and_section_tabs(self):
        response = self.client.get('/studio/settings/')
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()

        self.assertIn('data-settings-filter', body)
        self.assertIn('data-settings-filter-empty', body)
        self.assertIn('role="tablist"', body)
        self.assertIn('data-section-nav-item="auth"', body)
        self.assertIn('data-section-nav-item="payments"', body)
        self.assertIn('data-section-nav-item="messaging"', body)

    def test_only_first_section_is_initially_unhidden(self):
        response = self.client.get('/studio/settings/')
        body = response.content.decode()

        auth_section = re.search(
            r'<section[^>]*data-settings-section="auth"[^>]*>',
            body,
            re.DOTALL,
        )
        payments_section = re.search(
            r'<section[^>]*data-settings-section="payments"[^>]*>',
            body,
            re.DOTALL,
        )
        self.assertIsNotNone(auth_section)
        self.assertIsNotNone(payments_section)
        self.assertNotIn('hidden', auth_section.group(0))
        self.assertIn('hidden', payments_section.group(0))

    def test_search_metadata_covers_keys_descriptions_sections_and_providers(self):
        response = self.client.get('/studio/settings/')
        body = response.content.decode()

        self.assertIn('data-section-search-text="Messaging messaging', body)
        self.assertIn('data-card-search-text="Slack slack"', body)
        self.assertIn(
            'data-field-search-text="SLACK_BOT_TOKEN Slack bot user OAuth token',
            body,
        )
        self.assertIn('data-card-search-text="Google OAuth Google google"', body)
        self.assertIn(
            'data-field-search-text="STRIPE_WEBHOOK_SECRET Verifies that webhook',
            body,
        )


class SettingsDashboardAccessControlTest(TestCase):
    """The redesigned settings dashboard keeps the existing staff gate."""

    @classmethod
    def setUpTestData(cls):
        cls.regular_user = User.objects.create_user(
            email='member@test.com', password='testpass', is_staff=False,
        )

    def test_anonymous_user_is_redirected(self):
        response = self.client.get('/studio/settings/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)
        self.assertIn('next=/studio/settings/', response.url)

    def test_non_staff_user_gets_403_without_settings_keys(self):
        self.client.login(email='member@test.com', password='testpass')
        response = self.client.get('/studio/settings/')

        self.assertEqual(response.status_code, 403)
        self.assertNotContains(response, 'SLACK_BOT_TOKEN', status_code=403)
        self.assertNotContains(response, 'STRIPE_SECRET_KEY', status_code=403)
