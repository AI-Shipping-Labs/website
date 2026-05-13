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
        page.fill("#password-reset-email", email)
        page.click("#password-reset-request-submit")

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
        assert _email_log_count(django_db_blocker, email) == 0

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
