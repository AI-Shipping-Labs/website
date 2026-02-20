from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import User


class LoginViewTest(TestCase):
    """Tests for the login page."""

    def test_login_page_returns_200(self):
        response = self.client.get("/accounts/login/")
        self.assertEqual(response.status_code, 200)

    def test_login_page_uses_correct_template(self):
        response = self.client.get("/accounts/login/")
        self.assertTemplateUsed(response, "accounts/login.html")

    def test_login_page_contains_google_button(self):
        response = self.client.get("/accounts/login/")
        content = response.content.decode()
        self.assertIn("Sign in with Google", content)

    def test_login_page_contains_github_button(self):
        response = self.client.get("/accounts/login/")
        content = response.content.decode()
        self.assertIn("Sign in with GitHub", content)

    def test_login_page_contains_google_oauth_link(self):
        response = self.client.get("/accounts/login/")
        content = response.content.decode()
        self.assertIn("/accounts/google/login/", content)

    def test_login_page_contains_github_oauth_link(self):
        response = self.client.get("/accounts/login/")
        content = response.content.decode()
        self.assertIn("/accounts/github/login/", content)

    def test_login_page_includes_header(self):
        response = self.client.get("/accounts/login/")
        content = response.content.decode()
        self.assertIn("AI Shipping Labs", content)

    def test_login_page_includes_footer(self):
        response = self.client.get("/accounts/login/")
        content = response.content.decode()
        self.assertIn("</footer>", content)

    def test_login_page_has_tailwind(self):
        response = self.client.get("/accounts/login/")
        content = response.content.decode()
        self.assertIn("tailwindcss", content)

    def test_login_page_redirects_authenticated_user(self):
        user = User.objects.create_user(email="test@example.com")
        self.client.force_login(user)
        response = self.client.get("/accounts/login/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/")

    def test_login_url_name(self):
        url = reverse("account_login")
        self.assertEqual(url, "/accounts/login/")


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


class ProtectedPageRedirectTest(TestCase):
    """Tests that protected pages redirect to login."""

    def test_admin_redirects_unauthenticated_to_login(self):
        """Django admin should redirect unauthenticated users."""
        response = self.client.get("/admin/", follow=False)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response.url)


class HeaderAuthDisplayTest(TestCase):
    """Tests for authentication-aware header display."""

    def test_header_shows_sign_in_for_anonymous(self):
        response = self.client.get("/")
        content = response.content.decode()
        self.assertIn("Sign in", content)
        self.assertNotIn("Log out", content)

    def test_header_shows_logout_for_authenticated(self):
        user = User.objects.create_user(email="test@example.com")
        self.client.force_login(user)
        response = self.client.get("/")
        content = response.content.decode()
        self.assertIn("Log out", content)
        self.assertIn("test@example.com", content)

    def test_header_shows_email_for_authenticated(self):
        user = User.objects.create_user(email="user@demo.com")
        self.client.force_login(user)
        response = self.client.get("/")
        content = response.content.decode()
        self.assertIn("user@demo.com", content)
