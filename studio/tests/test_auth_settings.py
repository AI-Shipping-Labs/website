"""Tests for the Studio Auth & Login settings section (issue #322).

Covers the dashboard rendering both zones, the auth-card content
(callback URL, scopes, status badge), the save endpoint upserting a
``SocialApp`` row, and the login page hiding "Sign in with X" buttons
when the matching provider has no credentials.
"""

from allauth.socialaccount.models import SocialApp
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.test import TestCase, override_settings

from integrations.models import IntegrationSetting

User = get_user_model()


@override_settings(SITE_BASE_URL='https://test.aishippinglabs.com')
class AuthLoginDashboardTest(TestCase):
    """Dashboard renders both zones with the expected auth-card content."""

    @classmethod
    def setUpTestData(cls):
        cls.staff_user = User.objects.create_user(
            email='admin@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='admin@test.com', password='testpass')

    def test_dashboard_renders_two_zones(self):
        response = self.client.get('/studio/settings/')
        self.assertEqual(response.status_code, 200)
        # Both section anchors must be present so the operator can see
        # the Auth & Login zone above the Integrations zone.
        self.assertContains(response, 'id="auth-login"')
        self.assertContains(response, 'id="integrations"')

    def test_auth_zone_renders_three_provider_cards(self):
        response = self.client.get('/studio/settings/')
        self.assertContains(response, 'id="auth-google"')
        self.assertContains(response, 'id="auth-github"')
        self.assertContains(response, 'id="auth-slack"')

    def test_auth_providers_in_context(self):
        response = self.client.get('/studio/settings/')
        provider_keys = [p['provider'] for p in response.context['auth_providers']]
        self.assertEqual(provider_keys, ['google', 'github', 'slack'])

    def test_callback_urls_use_site_base_url(self):
        response = self.client.get('/studio/settings/')
        providers = {p['provider']: p for p in response.context['auth_providers']}
        self.assertEqual(
            providers['google']['callback_url'],
            'https://test.aishippinglabs.com/accounts/google/login/callback/',
        )
        self.assertEqual(
            providers['github']['callback_url'],
            'https://test.aishippinglabs.com/accounts/github/login/callback/',
        )
        self.assertEqual(
            providers['slack']['callback_url'],
            'https://test.aishippinglabs.com/accounts/slack/login/callback/',
        )

    def test_scopes_sourced_from_socialaccount_providers_setting(self):
        response = self.client.get('/studio/settings/')
        providers = {p['provider']: p for p in response.context['auth_providers']}
        # SOCIALACCOUNT_PROVIDERS in website/settings.py:
        # google: ['profile', 'email'], github: ['user:email'],
        # slack: ['openid', 'profile', 'email'].
        self.assertEqual(providers['google']['scopes'], ['profile', 'email'])
        self.assertEqual(providers['github']['scopes'], ['user:email'])
        self.assertEqual(providers['slack']['scopes'], ['openid', 'profile', 'email'])

    def test_status_not_configured_when_no_socialapp(self):
        response = self.client.get('/studio/settings/')
        providers = {p['provider']: p for p in response.context['auth_providers']}
        for key in ('google', 'github', 'slack'):
            self.assertFalse(providers[key]['is_configured'], f'{key} should not be configured')

    def test_status_configured_when_socialapp_has_both_credentials(self):
        SocialApp.objects.create(
            provider='google', name='Google',
            client_id='cid-123', secret='sec-456',
        )
        response = self.client.get('/studio/settings/')
        providers = {p['provider']: p for p in response.context['auth_providers']}
        self.assertTrue(providers['google']['is_configured'])

    def test_status_not_configured_when_only_client_id_set(self):
        # Only one of two fields populated — binary "Not configured" per
        # locked decision (no Partial state for OAuth cards).
        SocialApp.objects.create(
            provider='google', name='Google', client_id='cid-only', secret='',
        )
        response = self.client.get('/studio/settings/')
        providers = {p['provider']: p for p in response.context['auth_providers']}
        self.assertFalse(providers['google']['is_configured'])

    def test_dashboard_still_shows_all_integration_groups(self):
        response = self.client.get('/studio/settings/')
        group_names = [g['name'] for g in response.context['groups']]
        # Sanity check — the reorganisation must NOT drop integrations.
        self.assertIn('stripe', group_names)
        self.assertIn('zoom', group_names)
        self.assertIn('slack', group_names)
        self.assertIn('github', group_names)


class AuthLoginAccessControlTest(TestCase):
    """Only staff can view or POST to auth-login settings."""

    @classmethod
    def setUpTestData(cls):
        cls.regular_user = User.objects.create_user(
            email='user@test.com', password='testpass', is_staff=False,
        )

    def test_non_staff_cannot_view_dashboard(self):
        self.client.login(email='user@test.com', password='testpass')
        response = self.client.get('/studio/settings/')
        self.assertEqual(response.status_code, 403)

    def test_anonymous_redirected_to_login(self):
        response = self.client.get('/studio/settings/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)

    def test_non_staff_cannot_save_auth_provider(self):
        self.client.login(email='user@test.com', password='testpass')
        response = self.client.post(
            '/studio/settings/auth/google/save/',
            {'client_id': 'sneaky', 'client_secret': 'sneaky'},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(SocialApp.objects.filter(provider='google').count(), 0)

    def test_anonymous_cannot_save_auth_provider(self):
        response = self.client.post(
            '/studio/settings/auth/google/save/',
            {'client_id': 'sneaky', 'client_secret': 'sneaky'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)
        self.assertEqual(SocialApp.objects.filter(provider='google').count(), 0)


class AuthProviderSaveTest(TestCase):
    """POST /studio/settings/auth/<provider>/save/ upserts SocialApp."""

    @classmethod
    def setUpTestData(cls):
        cls.staff_user = User.objects.create_user(
            email='admin@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='admin@test.com', password='testpass')

    def test_save_creates_socialapp_with_correct_fields(self):
        response = self.client.post(
            '/studio/settings/auth/google/save/',
            {'client_id': 'goog-test-id', 'client_secret': 'goog-test-secret'},
        )
        self.assertEqual(response.status_code, 302)
        app = SocialApp.objects.get(provider='google')
        self.assertEqual(app.client_id, 'goog-test-id')
        self.assertEqual(app.secret, 'goog-test-secret')
        self.assertEqual(app.name, 'Google')

    def test_save_attaches_current_site(self):
        self.client.post(
            '/studio/settings/auth/google/save/',
            {'client_id': 'cid', 'client_secret': 'sec'},
        )
        app = SocialApp.objects.get(provider='google')
        current_site = Site.objects.get_current()
        self.assertIn(current_site, app.sites.all())

    def test_save_does_not_duplicate_on_second_post(self):
        # First save creates, second save updates the same row.
        self.client.post(
            '/studio/settings/auth/github/save/',
            {'client_id': 'old', 'client_secret': 'old-sec'},
        )
        self.client.post(
            '/studio/settings/auth/github/save/',
            {'client_id': 'gh-new-id', 'client_secret': 'gh-new-sec'},
        )
        apps = SocialApp.objects.filter(provider='github')
        self.assertEqual(apps.count(), 1)
        self.assertEqual(apps.first().client_id, 'gh-new-id')

    def test_save_empty_values_clears_credentials(self):
        # Clearing credentials is the documented way to disable a
        # provider. Row stays so the operator history is preserved.
        SocialApp.objects.create(
            provider='slack', name='Slack',
            client_id='old-cid', secret='old-sec',
        )
        self.client.post(
            '/studio/settings/auth/slack/save/',
            {'client_id': '', 'client_secret': ''},
        )
        app = SocialApp.objects.get(provider='slack')
        self.assertEqual(app.client_id, '')
        self.assertEqual(app.secret, '')

    def test_save_unknown_provider_does_not_create_row(self):
        response = self.client.post(
            '/studio/settings/auth/twitter/save/',
            {'client_id': 'x', 'client_secret': 'y'},
        )
        # Whitelist: redirect back to settings with an error message,
        # but no SocialApp row for the unknown provider.
        self.assertEqual(response.status_code, 302)
        self.assertEqual(SocialApp.objects.filter(provider='twitter').count(), 0)

    def test_save_redirects_to_provider_anchor(self):
        response = self.client.post(
            '/studio/settings/auth/google/save/',
            {'client_id': 'cid', 'client_secret': 'sec'},
        )
        self.assertIn('#auth-google', response.url)

class IntegrationSaveRegressionTest(TestCase):
    """Existing IntegrationSetting save flow still works after reorganisation."""

    @classmethod
    def setUpTestData(cls):
        cls.staff_user = User.objects.create_user(
            email='admin@test.com', password='testpass', is_staff=True,
        )

    def test_stripe_save_does_not_touch_socialapp(self):
        self.client.login(email='admin@test.com', password='testpass')
        response = self.client.post('/studio/settings/stripe/save/', {
            'STRIPE_SECRET_KEY': 'sk_test_x',
            'STRIPE_WEBHOOK_SECRET': '',
            'STRIPE_PUBLISHABLE_KEY': 'pk_test_new',
            'STRIPE_CHECKOUT_ENABLED': 'true',
            'STRIPE_CUSTOMER_PORTAL_URL': '',
            'confirm_update': 'on',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            IntegrationSetting.objects.get(key='STRIPE_PUBLISHABLE_KEY').value,
            'pk_test_new',
        )
        # Saving an integration must not create OAuth login rows.
        self.assertEqual(SocialApp.objects.count(), 0)


class LoginPageGatingTest(TestCase):
    """Login page hides "Sign in with X" buttons when SocialApp empty."""

    def test_buttons_hidden_when_no_socialapp(self):
        response = self.client.get('/accounts/login/')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Sign in with Google')
        self.assertNotContains(response, 'Sign in with GitHub')
        self.assertNotContains(response, 'Sign in with Slack')

    def test_buttons_hidden_when_client_id_empty(self):
        # Row exists but with empty credentials — the documented
        # operator off-switch.
        SocialApp.objects.create(
            provider='google', name='Google', client_id='', secret='',
        )
        response = self.client.get('/accounts/login/')
        self.assertNotContains(response, 'Sign in with Google')

    def test_button_visible_when_provider_configured(self):
        SocialApp.objects.create(
            provider='google', name='Google',
            client_id='cid', secret='sec',
        )
        response = self.client.get('/accounts/login/')
        self.assertContains(response, 'Sign in with Google')

    def test_only_configured_providers_show(self):
        # Only Google is configured — the other two buttons stay hidden.
        SocialApp.objects.create(
            provider='google', name='Google',
            client_id='cid', secret='sec',
        )
        response = self.client.get('/accounts/login/')
        self.assertContains(response, 'Sign in with Google')
        self.assertNotContains(response, 'Sign in with GitHub')
        self.assertNotContains(response, 'Sign in with Slack')


class IntegrationDescriptionRewriteTest(TestCase):
    """Section E description rewrites land in the registry."""

    def test_stripe_webhook_description_explains_what_it_verifies(self):
        from integrations.settings_registry import get_group_by_name
        stripe = get_group_by_name('stripe')
        webhook_field = next(
            k for k in stripe['keys'] if k['key'] == 'STRIPE_WEBHOOK_SECRET'
        )
        # Old text was "Stripe webhook signing secret" — new text must
        # explain the verification role.
        self.assertIn('webhook callbacks', webhook_field['description'].lower())
        self.assertIn('stripe', webhook_field['description'].lower())

    def test_slack_bot_description_mentions_posting(self):
        from integrations.settings_registry import get_group_by_name
        slack = get_group_by_name('slack')
        token_field = next(
            k for k in slack['keys'] if k['key'] == 'SLACK_BOT_TOKEN'
        )
        # The Slack bot integration must be unmistakable from the Slack
        # OAuth login provider — its description talks about posting and
        # reading channel events, not user sign-in.
        desc = token_field['description'].lower()
        self.assertTrue('post' in desc or 'announce' in desc)
        self.assertNotIn('sign in', desc)
