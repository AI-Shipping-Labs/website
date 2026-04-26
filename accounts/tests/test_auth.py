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
