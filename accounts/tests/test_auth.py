from allauth.socialaccount.models import SocialApp
from django.contrib.sites.models import Site
from django.test import TestCase, tag
from django.urls import reverse

from accounts.models import User


def _configure_provider(provider, name):
    """Create a configured ``SocialApp`` for ``provider`` so the login
    template renders the matching "Sign in with X" button. Issue #322
    gates each button on a non-empty ``client_id``.
    """
    app = SocialApp.objects.create(
        provider=provider,
        name=name,
        client_id=f'{provider}-cid',
        secret=f'{provider}-secret',
    )
    app.sites.add(Site.objects.get_current())
    return app


@tag('core')
class LoginViewTest(TestCase):
    """Tests for the login page."""

    def test_login_page_returns_200(self):
        response = self.client.get("/accounts/login/")
        self.assertEqual(response.status_code, 200)

    def test_login_page_uses_correct_template(self):
        response = self.client.get("/accounts/login/")
        self.assertTemplateUsed(response, "accounts/login.html")

    def test_login_page_contains_google_button(self):
        _configure_provider('google', 'Google')
        response = self.client.get("/accounts/login/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'href="/accounts/google/login/"')
        self.assertContains(response, "Sign in with Google")

    def test_login_page_contains_github_button(self):
        _configure_provider('github', 'GitHub')
        response = self.client.get("/accounts/login/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'href="/accounts/github/login/"')
        self.assertContains(response, "Sign in with GitHub")

    def test_login_page_contains_slack_button(self):
        _configure_provider('slack', 'Slack')
        response = self.client.get("/accounts/login/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'href="/accounts/slack/login/"')
        self.assertContains(response, "Sign in with Slack")

    def test_login_page_redirects_authenticated_user(self):
        user = User.objects.create_user(email="test@example.com")
        self.client.force_login(user)
        response = self.client.get("/accounts/login/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/")


@tag('core')
class SharedAuthTemplateTest(TestCase):
    """Tests for the shared login/register auth shell and OAuth partials."""

    def test_login_uses_shared_auth_includes(self):
        response = self.client.get("/accounts/login/")

        self.assertTemplateUsed(response, "accounts/includes/_auth_card.html")
        self.assertTemplateUsed(response, "accounts/includes/_login_form.html")
        self.assertTemplateUsed(response, "accounts/includes/_oauth_providers.html")
        self.assertTemplateUsed(response, "accounts/includes/_legal_footer.html")

    def test_register_uses_shared_auth_includes(self):
        response = self.client.get("/accounts/register/")

        self.assertTemplateUsed(response, "accounts/includes/_auth_card.html")
        self.assertTemplateUsed(response, "accounts/includes/_register_form.html")
        self.assertTemplateUsed(response, "accounts/includes/_oauth_providers.html")
        self.assertTemplateUsed(response, "accounts/includes/_legal_footer.html")

    def test_login_hides_oauth_area_when_all_providers_disabled(self):
        response = self.client.get("/accounts/login/")

        self.assertNotContains(response, 'data-auth-oauth-divider')
        self.assertNotContains(response, "Sign in with Google")
        self.assertNotContains(response, "Sign in with GitHub")
        self.assertNotContains(response, "Sign in with Slack")

    def test_register_hides_oauth_area_when_all_providers_disabled(self):
        response = self.client.get("/accounts/register/")

        self.assertNotContains(response, 'data-auth-oauth-divider')
        self.assertNotContains(response, "Sign up with Google")
        self.assertNotContains(response, "Sign up with GitHub")
        self.assertNotContains(response, "Sign up with Slack")

    def test_login_renders_only_enabled_provider_buttons(self):
        _configure_provider('google', 'Google')

        response = self.client.get("/accounts/login/")

        self.assertContains(response, 'data-auth-oauth-divider')
        self.assertContains(response, 'href="/accounts/google/login/"')
        self.assertContains(response, "Sign in with Google")
        self.assertNotContains(response, "Sign in with GitHub")
        self.assertNotContains(response, "Sign in with Slack")

    def test_register_renders_only_enabled_provider_buttons(self):
        _configure_provider('slack', 'Slack')

        response = self.client.get("/accounts/register/")

        self.assertContains(response, 'data-auth-oauth-divider')
        self.assertContains(response, 'href="/accounts/slack/login/"')
        self.assertContains(response, "Sign up with Slack")
        self.assertNotContains(response, "Sign up with Google")
        self.assertNotContains(response, "Sign up with GitHub")


@tag('core')
class LogoutViewTest(TestCase):
    """Tests for the logout flow."""

    def test_logout_redirects_to_homepage(self):
        user = User.objects.create_user(email="test@example.com")
        self.client.force_login(user)
        response = self.client.get("/accounts/logout/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/")

    def test_logout_ends_session(self):
        user = User.objects.create_user(email="test@example.com")
        self.client.force_login(user)
        self.client.get("/accounts/logout/")
        # After logout, accessing a page should show anonymous user
        response = self.client.get("/")
        self.assertFalse(response.wsgi_request.user.is_authenticated)

    def test_logout_url_name(self):
        url = reverse("account_logout")
        self.assertEqual(url, "/accounts/logout/")


@tag('core')
class ProtectedPageRedirectTest(TestCase):
    """Tests that protected pages redirect to login."""

    def test_admin_redirects_unauthenticated_to_login(self):
        """Django admin should redirect unauthenticated users."""
        response = self.client.get("/admin/", follow=False)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response.url)


@tag('core')
class HeaderAuthDisplayTest(TestCase):
    """Tests for authentication-aware header display."""

    def test_header_shows_sign_in_for_anonymous(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("Sign in", content)
        self.assertNotIn("Log out", content)

    def test_header_shows_logout_for_authenticated(self):
        user = User.objects.create_user(email="test@example.com")
        self.client.force_login(user)
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("Log out", content)
        self.assertIn("test@example.com", content)

    def test_header_shows_email_for_authenticated(self):
        user = User.objects.create_user(email="user@demo.com")
        self.client.force_login(user)
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("user@demo.com", content)
