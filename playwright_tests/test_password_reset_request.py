"""Playwright coverage for the password-reset request entry point (issue #536)."""

import os
import uuid

import pytest

from playwright_tests.conftest import (
    DEFAULT_PASSWORD,
    auth_context,
    create_user,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

# Issue #656: this module uses local-only fixtures (DB seeding,
# session-cookie injection, etc.) and cannot run against the
# deployed dev environment. See _docs/testing-guidelines.md.
pytestmark = [pytest.mark.local_only, pytest.mark.core]

SUCCESS_MESSAGE = (
    "If an account exists for that email, we\u2019ll send password reset "
    "instructions shortly."
)


def _email(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:8]}@test.com"


def _seed_user(db_blocker, email, password=DEFAULT_PASSWORD):
    with db_blocker.unblock():
        user = create_user(email=email, password=password)
        return user.pk


def _configure_google_oauth(db_blocker):
    with db_blocker.unblock():
        from allauth.socialaccount.models import SocialApp
        from django.contrib.sites.models import Site
        from django.db import connection

        app = SocialApp.objects.create(
            provider="google",
            name="Google",
            client_id="google-cid",
            secret="google-secret",
        )
        app.sites.add(Site.objects.get_current())
        connection.close()


def _email_log_count(db_blocker, email):
    with db_blocker.unblock():
        from django.db import connection

        from email_app.models import EmailLog

        count = EmailLog.objects.filter(
            user__email=email,
            email_type="password_reset",
        ).count()
        connection.close()
        return count


@pytest.mark.django_db(transaction=True)
class TestPasswordResetRequest:
    def test_anonymous_member_requests_reset_link(
        self, django_server, page, django_db_blocker
    ):
        email = _email("forgot-existing")
        _seed_user(django_db_blocker, email)
        reset_calls = []
        page.on(
            "request",
            lambda request: reset_calls.append(request)
            if request.url.endswith("/api/password-reset-request")
            else None,
        )

        page.goto(f"{django_server}/accounts/login/", wait_until="domcontentloaded")
        page.click("#forgot-password-link")
        page.wait_for_url(f"{django_server}/accounts/password-reset-request")
        auth_card = page.locator('[data-testid="auth-card"]')
        assert auth_card.get_by_role(
            "heading", name="Reset your password", exact=True
        ).is_visible()
        assert auth_card.get_by_role(
            "link", name="Back to sign in", exact=True
        ).is_visible()
        assert page.locator("#newsletter").count() == 0
        assert page.locator("[data-auth-oauth-divider]").count() == 0
        assert "By signing" not in auth_card.inner_text()

        pending_routes = []
        page.route(
            "**/api/password-reset-request",
            lambda route: pending_routes.append(route),
        )
        page.fill("#password-reset-email", email)
        page.click("#password-reset-request-submit")
        page.wait_for_function(
            "document.querySelector('#password-reset-request-submit-text').textContent === 'Sending...'"
        )
        assert page.locator("#password-reset-request-submit").is_disabled()
        assert len(pending_routes) == 1
        pending_routes[0].continue_()

        success = page.locator("#password-reset-request-success")
        success.wait_for(state="visible")
        assert success.inner_text() == SUCCESS_MESSAGE
        assert page.locator("#password-reset-request-submit").inner_text() == (
            "Send reset link"
        )
        assert len(reset_calls) == 1
        assert _email_log_count(django_db_blocker, email) == 1

    def test_unknown_email_gets_same_success_without_email_log(
        self, django_server, page, django_db_blocker
    ):
        email = _email("not-a-user")

        page.goto(
            f"{django_server}/accounts/password-reset-request",
            wait_until="domcontentloaded",
        )
        page.fill("#password-reset-email", email)
        page.click("#password-reset-request-submit")

        success = page.locator("#password-reset-request-success")
        success.wait_for(state="visible")
        assert success.inner_text() == SUCCESS_MESSAGE
        assert "not found" not in page.content().lower()
        assert "does not exist" not in page.content().lower()
        assert page.locator("#newsletter").count() == 0
        assert _email_log_count(django_db_blocker, email) == 0

    def test_configured_oauth_is_isolated_from_reset_page(
        self, django_server, page, django_db_blocker
    ):
        _configure_google_oauth(django_db_blocker)

        page.goto(
            f"{django_server}/accounts/password-reset-request",
            wait_until="domcontentloaded",
        )
        auth_card = page.locator('[data-testid="auth-card"]')
        assert auth_card.locator("#password-reset-email").is_visible()
        assert auth_card.locator("[data-auth-oauth-divider]").count() == 0
        assert auth_card.get_by_role(
            "link", name="Sign in with Google", exact=True
        ).count() == 0
        assert "By signing in, you agree" not in auth_card.inner_text()

        page.get_by_role("link", name="Back to sign in", exact=True).click()
        page.wait_for_url(f"{django_server}/accounts/login/")
        login_card = page.locator('[data-testid="auth-card"]')
        assert login_card.get_by_role(
            "link", name="Sign in with Google", exact=True
        ).is_visible()
        assert "By signing in, you agree to our" in login_card.inner_text()

    def test_registration_and_login_use_sentence_case_copy(
        self, django_server, page, django_db_blocker
    ):
        _configure_google_oauth(django_db_blocker)

        page.goto(f"{django_server}/accounts/register/", wait_until="domcontentloaded")
        register_card = page.locator('[data-testid="auth-card"]')
        assert page.title() == "Create account | AI Shipping Labs"
        assert register_card.get_by_role(
            "heading", name="Create account", exact=True
        ).is_visible()
        assert page.locator("#register-submit").inner_text() == "Create account"
        assert page.locator("#register-submit").get_attribute(
            "data-idle-text"
        ) == "Create account"
        assert register_card.get_by_role(
            "link", name="Sign up with Google", exact=True
        ).is_visible()
        assert "By creating an account, you agree to our" in register_card.inner_text()

        page.locator("#login-link").click()
        page.wait_for_url(f"{django_server}/accounts/login/")
        login_card = page.locator('[data-testid="auth-card"]')
        assert page.title() == "Sign in | AI Shipping Labs"
        assert login_card.get_by_role(
            "heading", name="Sign in", exact=True
        ).is_visible()
        assert page.locator("#login-submit").inner_text() == "Sign in"
        assert login_card.get_by_role(
            "link", name="Sign in with Google", exact=True
        ).is_visible()
        assert "By signing in, you agree to our" in login_card.inner_text()

        page.locator("#register-link").click()
        page.wait_for_url(f"{django_server}/accounts/register/")
        assert page.get_by_role(
            "heading", name="Create account", exact=True
        ).is_visible()

    def test_authenticated_newsletter_member_uses_prefilled_reset_form(
        self, django_server, browser, django_db_blocker
    ):
        email = _email("newsletter-prefill")
        _seed_user(django_db_blocker, email)
        context = auth_context(browser, email)
        page = context.new_page()

        try:
            page.goto(
                f"{django_server}/accounts/password-reset-request?email={email}",
                wait_until="domcontentloaded",
            )
            assert page.url.endswith(
                f"/accounts/password-reset-request?email={email}"
            )
            assert page.locator("#password-reset-email").input_value() == email
            assert page.locator("#password-reset-request-form").is_visible()
            assert page.locator("#newsletter").count() == 0
            assert page.locator("[data-auth-oauth-divider]").count() == 0
            assert "By signing" not in page.locator(
                '[data-testid="auth-card"]'
            ).inner_text()
        finally:
            context.close()

    def test_empty_reset_request_can_be_fixed(self, django_server, page):
        page.goto(
            f"{django_server}/accounts/password-reset-request",
            wait_until="domcontentloaded",
        )

        page.click("#password-reset-request-submit")

        error = page.locator("#password-reset-request-error")
        error.wait_for(state="visible")
        assert error.inner_text() == "Email is required"
        assert page.locator("#password-reset-request-submit").inner_text() == (
            "Send reset link"
        )
        assert not page.locator("#password-reset-request-submit").is_disabled()

        page.fill("#password-reset-email", _email("fixed-reset"))
        page.click("#password-reset-request-submit")

        success = page.locator("#password-reset-request-success")
        success.wait_for(state="visible")
        assert success.inner_text() == SUCCESS_MESSAGE

    def test_existing_member_completes_reset_and_signs_in(
        self, django_server, page, django_db_blocker
    ):
        email = _email("reset-complete")
        user_pk = _seed_user(django_db_blocker, email, password="OldPass123!")

        page.goto(
            f"{django_server}/accounts/password-reset-request",
            wait_until="domcontentloaded",
        )
        page.fill("#password-reset-email", email)
        page.click("#password-reset-request-submit")
        page.locator("#password-reset-request-success").wait_for(state="visible")
        assert _email_log_count(django_db_blocker, email) == 1

        with django_db_blocker.unblock():
            from django.db import connection

            from accounts.views.auth import _generate_password_reset_token

            token = _generate_password_reset_token(user_pk)
            connection.close()

        page.goto(
            f"{django_server}/api/password-reset?token={token}",
            wait_until="domcontentloaded",
        )
        assert page.locator("#reset-form").is_visible()
        page.fill("#new-password", "NewPass123!")
        page.fill("#confirm-password", "NewPass123!")
        page.click("#reset-submit")
        page.locator("#reset-success").wait_for(state="visible")
        assert "Password has been reset successfully." in page.locator(
            "#reset-success"
        ).inner_text()

        page.goto(f"{django_server}/accounts/login/", wait_until="domcontentloaded")
        page.fill("#login-email", email)
        page.fill("#login-password", "NewPass123!")
        page.click("#login-submit")
        page.wait_for_url(f"{django_server}/", timeout=10000)

    def test_invalid_reset_link_has_sign_in_path(self, django_server, page):
        page.goto(
            f"{django_server}/api/password-reset?token=invalid-token",
            wait_until="domcontentloaded",
        )

        assert page.locator("#token-error").is_visible()
        assert "Invalid password reset link." in page.locator("#token-error").inner_text()
        back_link = page.get_by_role("link", name="Back to Sign In").first
        assert back_link.get_attribute("href") == "/accounts/login/"

    def test_signed_in_member_redirects_to_account(
        self, django_server, browser, django_db_blocker
    ):
        email = _email("signed-in-reset")
        _seed_user(django_db_blocker, email)
        context = auth_context(browser, email)
        page = context.new_page()

        page.goto(f"{django_server}/accounts/password-reset-request")
        page.wait_for_url(f"{django_server}/account/")
        assert page.locator("#password-reset-request-form").count() == 0
        context.close()
