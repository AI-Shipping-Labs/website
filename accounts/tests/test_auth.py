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

    def test_login_page_redirects_authenticated_user_to_safe_next(self):
        user = User.objects.create_user(email="test@example.com")
        self.client.force_login(user)
        response = self.client.get("/accounts/login/?next=/events/demo")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/events/demo")

    def test_login_page_ignores_external_next_for_authenticated_user(self):
        user = User.objects.create_user(email="test@example.com")
        self.client.force_login(user)
        response = self.client.get("/accounts/login/?next=https://evil.example")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/")

    def test_login_page_links_to_password_reset_request_page(self):
        response = self.client.get("/accounts/login/")
        content = response.content.decode()
        link_start = content.index('id="forgot-password-link"')
        forgot_link = content[max(0, link_start - 200):link_start + 200]

        self.assertContains(response, 'id="forgot-password-link"')
        self.assertContains(response, 'href="/accounts/password-reset-request"')
        self.assertNotIn("?view=forgot", forgot_link)
        self.assertNotIn("onclick=", forgot_link)


@tag('core')
class PasswordResetRequestViewTest(TestCase):
    """Tests for the public password-reset request page."""

    def test_anonymous_user_can_open_request_page(self):
        response = self.client.get("/accounts/password-reset-request")

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "accounts/password_reset_request.html")
        self.assertContains(response, "Reset your password")
        self.assertContains(
            response,
            "Enter your account email and we'll send you a reset link.",
        )
        self.assertContains(response, 'type="email"')
        self.assertContains(response, 'id="password-reset-email"')
        self.assertContains(response, 'name="email"')
        self.assertContains(response, "text-base")
        self.assertContains(response, "Send reset link")
        self.assertContains(response, "Sending...")
        self.assertContains(
            response,
            "If that email is on file, a reset link is on its way.",
        )
        self.assertContains(response, 'href="/accounts/login/"')
        self.assertContains(response, "Back to sign in")

    def test_request_page_uses_auth_card_padding(self):
        response = self.client.get("/accounts/password-reset-request")
        self.assertContains(response, "p-5 sm:p-8")

    def test_request_page_posts_to_existing_api_with_csrf(self):
        response = self.client.get("/accounts/password-reset-request")
        content = response.content.decode()

        self.assertIn("fetch('/api/password-reset-request'", content)
        self.assertIn("'X-CSRFToken': window.authHelpers.getCsrfToken()", content)
        self.assertIn("window.authHelpers.setPendingState", content)
        self.assertIn("An error occurred. Please try again.", content)

    def test_authenticated_user_redirects_to_account(self):
        user = User.objects.create_user(email="reset-auth@example.com")
        self.client.force_login(user)

        response = self.client.get("/accounts/password-reset-request")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/account/")

    def test_request_page_url_name(self):
        url = reverse("account_password_reset_request")
        self.assertEqual(url, "/accounts/password-reset-request")


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

    def test_oauth_links_preserve_safe_next(self):
        _configure_provider('google', 'Google')

        response = self.client.get("/accounts/login/?next=/courses/demo")

        self.assertContains(
            response,
            'href="/accounts/google/login/?next=/courses/demo"',
        )

    def test_oauth_links_drop_unsafe_next(self):
        _configure_provider('github', 'GitHub')

        response = self.client.get("/accounts/login/?next=//evil.example/path")

        self.assertContains(response, 'href="/accounts/github/login/"')
        self.assertNotContains(response, 'next=//evil.example')

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
        """Without a ``next`` parameter, sign-out lands on ``/``."""
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

    def test_logout_honours_safe_next_for_public_path(self):
        """``?next=/events/<slug>`` keeps the user on the event page."""
        user = User.objects.create_user(email="test@example.com")
        self.client.force_login(user)
        response = self.client.get(
            "/accounts/logout/?next=/events/return-ctx-event"
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/events/return-ctx-event")

    def test_logout_honours_safe_next_for_course_unit(self):
        user = User.objects.create_user(email="test@example.com")
        self.client.force_login(user)
        response = self.client.get(
            "/accounts/logout/?next=/courses/return-ctx-course/intro/lesson"
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url, "/courses/return-ctx-course/intro/lesson"
        )

    def test_logout_honours_safe_next_for_workshop_path(self):
        user = User.objects.create_user(email="test@example.com")
        self.client.force_login(user)
        response = self.client.get(
            "/accounts/logout/?next=/workshops/sample-workshop"
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/workshops/sample-workshop")

    def test_logout_rejects_external_next(self):
        """An off-site ``next`` is sanitized and falls back to ``/``."""
        user = User.objects.create_user(email="test@example.com")
        self.client.force_login(user)
        response = self.client.get(
            "/accounts/logout/?next=https://evil.example.com/phish"
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/")

    def test_logout_rejects_protocol_relative_next(self):
        user = User.objects.create_user(email="test@example.com")
        self.client.force_login(user)
        response = self.client.get(
            "/accounts/logout/?next=//evil.example.com/phish"
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/")

    def test_logout_rejects_account_next(self):
        """``/account`` is a member-only surface — sign-out goes home."""
        user = User.objects.create_user(email="test@example.com")
        self.client.force_login(user)
        response = self.client.get("/accounts/logout/?next=/account/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/")

    def test_logout_rejects_studio_next(self):
        user = User.objects.create_user(
            email="staff@example.com", is_staff=True
        )
        self.client.force_login(user)
        response = self.client.get(
            "/accounts/logout/?next=/studio/articles/"
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/")

    def test_logout_rejects_admin_next(self):
        user = User.objects.create_user(
            email="staff@example.com", is_staff=True
        )
        self.client.force_login(user)
        response = self.client.get("/accounts/logout/?next=/admin/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/")

    def test_logout_rejects_accounts_login_next(self):
        """``/accounts/login`` would create a redirect loop."""
        user = User.objects.create_user(email="test@example.com")
        self.client.force_login(user)
        response = self.client.get(
            "/accounts/logout/?next=/accounts/login/"
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/")

    def test_logout_rejects_notifications_next(self):
        user = User.objects.create_user(email="test@example.com")
        self.client.force_login(user)
        response = self.client.get(
            "/accounts/logout/?next=/notifications"
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/")

    def test_logout_ends_session_even_with_next(self):
        """Session must be destroyed regardless of redirect target."""
        user = User.objects.create_user(email="test@example.com")
        self.client.force_login(user)
        self.client.get("/accounts/logout/?next=/events/some-event")
        # After logout, the user is anonymous on subsequent requests.
        response = self.client.get("/")
        self.assertFalse(response.wsgi_request.user.is_authenticated)


@tag('core')
class HeaderLogoutLinkTest(TestCase):
    """The Log out link in the header propagates the current path as ``next``."""

    def test_logout_link_includes_next_on_public_detail_page(self):
        user = User.objects.create_user(email="test@example.com")
        self.client.force_login(user)
        response = self.client.get("/about")
        self.assertEqual(response.status_code, 200)
        # Header is rendered on every page; logout link must carry the
        # current path so the user stays on /about after sign-out.
        self.assertContains(
            response,
            'href="/accounts/logout/?next=%2Fabout"',
        )

    def test_logout_link_omits_next_on_homepage(self):
        """Sign-out from ``/`` is already homepage — no ``next`` needed."""
        user = User.objects.create_user(email="test@example.com")
        self.client.force_login(user)
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        # The logout link still appears, but without a ``next`` query
        # parameter — there is nothing to preserve.
        self.assertContains(response, 'href="/accounts/logout/"')
        self.assertNotContains(response, 'logout/?next=%2F"')

    def test_logout_link_omits_next_on_account_page(self):
        """``/account`` is on the exclusion list — keep the URL clean."""
        user = User.objects.create_user(email="test@example.com")
        self.client.force_login(user)
        response = self.client.get("/account/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'href="/accounts/logout/"')
        self.assertNotContains(response, "logout/?next=%2Faccount")

    def test_logout_link_preserves_query_string(self):
        """``?next=`` should encode the full current path including the query."""
        user = User.objects.create_user(email="test@example.com")
        self.client.force_login(user)
        response = self.client.get("/pricing?tier=main")
        self.assertEqual(response.status_code, 200)
        # The pricing path with its query string is round-tripped through
        # urlencode, so the query separator becomes ``%3F`` and ``=``
        # becomes ``%3D``.
        self.assertContains(
            response,
            'href="/accounts/logout/?next=%2Fpricing%3Ftier%3Dmain"',
        )


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
