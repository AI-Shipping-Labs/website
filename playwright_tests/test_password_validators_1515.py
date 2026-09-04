"""Member-visible Django password validator journeys (issue #1515)."""

import os
import uuid

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import DEFAULT_PASSWORD, auth_context, create_user
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = [pytest.mark.local_only, pytest.mark.core]

TOO_COMMON = "This password is too common."
ENTIRELY_NUMERIC = "This password is entirely numeric."
TOO_SIMILAR = "too similar"


def _email(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:8]}@example.com"


def _seed_user(db_blocker, email, password=DEFAULT_PASSWORD):
    with db_blocker.unblock():
        user = create_user(email=email, password=password)
        return user.pk


def _user_exists(db_blocker, email):
    with db_blocker.unblock():
        from django.db import connection

        from accounts.models import User

        exists = User.objects.filter(email=email).exists()
        connection.close()
        return exists


def _sign_in(page, django_server, email, password):
    page.goto(f"{django_server}/accounts/login/", wait_until="domcontentloaded")
    page.fill("#login-email", email)
    page.fill("#login-password", password)
    page.click("#login-submit")
    page.wait_for_url(f"{django_server}/", timeout=10000)
    expect(page.locator('[data-testid="account-menu-trigger"]')).to_be_visible()


def _log_out(page):
    page.locator('[data-testid="account-menu-trigger"]').click()
    page.get_by_role("menuitem", name="Log out").click()
    expect(page.locator('[data-testid="account-menu-trigger"]')).to_have_count(0)


@pytest.mark.django_db(transaction=True)
class TestPasswordValidatorJourneys:
    @browser_journey
    def test_visitor_cannot_register_with_common_password(
        self, django_server, page, django_db_blocker
    ):
        email = "common-pw@example.com"
        page.goto(f"{django_server}/accounts/register/", wait_until="domcontentloaded")
        page.fill("#register-email", email)
        page.fill("#register-password", "password")
        page.fill("#register-password-confirm", "password")
        page.click("#register-submit")

        error = page.locator("#register-error")
        error.wait_for(state="visible")
        assert TOO_COMMON in error.inner_text()
        assert page.url.startswith(f"{django_server}/accounts/register/")
        expect(page.locator('[data-testid="account-menu-trigger"]')).to_have_count(0)
        assert not _user_exists(django_db_blocker, email)

        page.fill("#register-password", DEFAULT_PASSWORD)
        page.fill("#register-password-confirm", DEFAULT_PASSWORD)
        page.click("#register-submit")
        page.wait_for_url(f"{django_server}/", timeout=10000)
        expect(page.locator('[data-testid="account-menu-trigger"]')).to_be_visible()
        assert _user_exists(django_db_blocker, email)

    @browser_journey
    def test_visitor_cannot_register_with_numeric_password(
        self, django_server, page, django_db_blocker
    ):
        email = "numeric-pw@example.com"
        page.goto(f"{django_server}/accounts/register/", wait_until="domcontentloaded")
        page.fill("#register-email", email)
        page.fill("#register-password", "87654321")
        page.fill("#register-password-confirm", "87654321")
        page.click("#register-submit")

        error = page.locator("#register-error")
        error.wait_for(state="visible")
        assert ENTIRELY_NUMERIC in error.inner_text()
        assert page.url.startswith(f"{django_server}/accounts/register/")
        expect(page.locator('[data-testid="account-menu-trigger"]')).to_have_count(0)
        assert not _user_exists(django_db_blocker, email)

    @browser_journey
    def test_visitor_cannot_register_with_email_as_password(
        self, django_server, page, django_db_blocker
    ):
        email = "alice.builder@example.com"
        page.goto(f"{django_server}/accounts/register/", wait_until="domcontentloaded")
        page.fill("#register-email", email)
        page.fill("#register-password", email)
        page.fill("#register-password-confirm", email)
        page.click("#register-submit")

        error = page.locator("#register-error")
        error.wait_for(state="visible")
        assert TOO_SIMILAR in error.inner_text().lower()
        assert page.url.startswith(f"{django_server}/accounts/register/")
        expect(page.locator('[data-testid="account-menu-trigger"]')).to_have_count(0)
        assert not _user_exists(django_db_blocker, email)

    @browser_journey
    def test_member_cannot_change_to_common_password(
        self, django_server, browser, django_db_blocker
    ):
        email = _email("change-common")
        _seed_user(django_db_blocker, email)
        context = auth_context(browser, email)
        page = context.new_page()
        try:
            page.goto(f"{django_server}/account/", wait_until="domcontentloaded")
            page.fill("#current-password", DEFAULT_PASSWORD)
            page.fill("#new-password", "password")
            page.fill("#confirm-new-password", "password")
            page.locator("#change-password-form button[type='submit']").click()

            error = page.locator("#password-error")
            error.wait_for(state="visible")
            assert TOO_COMMON in error.inner_text()
            expect(page.locator("#password-success")).to_be_hidden()

            _log_out(page)
        finally:
            context.close()

        page = browser.new_page()
        try:
            _sign_in(page, django_server, email, DEFAULT_PASSWORD)
        finally:
            page.close()

    @browser_journey
    def test_member_changes_to_strong_password_and_signs_in(
        self, django_server, browser, django_db_blocker
    ):
        email = _email("change-strong")
        _seed_user(django_db_blocker, email)
        context = auth_context(browser, email)
        page = context.new_page()
        try:
            page.goto(f"{django_server}/account/", wait_until="domcontentloaded")
            page.fill("#current-password", DEFAULT_PASSWORD)
            page.fill("#new-password", "NewSecure456!")
            page.fill("#confirm-new-password", "NewSecure456!")
            page.locator("#change-password-form button[type='submit']").click()

            success = page.locator("#password-success")
            success.wait_for(state="visible")
            assert "password" in success.inner_text().lower()
            assert page.locator("#current-password").input_value() == ""
            assert page.locator("#new-password").input_value() == ""
            assert page.locator("#confirm-new-password").input_value() == ""

            _log_out(page)
        finally:
            context.close()

        page = browser.new_page()
        try:
            _sign_in(page, django_server, email, "NewSecure456!")
            page.goto(f"{django_server}/account/", wait_until="domcontentloaded")
            expect(page.locator("h1")).to_contain_text("Account")
        finally:
            page.close()

    @browser_journey
    def test_reset_link_rejects_numeric_password(
        self, django_server, page, django_db_blocker
    ):
        email = _email("reset-numeric")
        user_pk = _seed_user(django_db_blocker, email, password="OldPass123!")

        with django_db_blocker.unblock():
            from django.db import connection

            from accounts.views.auth import _generate_password_reset_token

            token = _generate_password_reset_token(user_pk)
            connection.close()

        page.goto(
            f"{django_server}/api/password-reset?token={token}",
            wait_until="domcontentloaded",
        )
        page.fill("#new-password", "87654321")
        page.fill("#confirm-password", "87654321")
        page.click("#reset-submit")

        error = page.locator("#reset-error")
        error.wait_for(state="visible")
        assert ENTIRELY_NUMERIC in error.inner_text()
        expect(page.locator("#reset-success")).to_be_hidden()

        _sign_in(page, django_server, email, "OldPass123!")

    @browser_journey
    def test_reset_link_sets_strong_password_and_signs_in(
        self, django_server, page, django_db_blocker
    ):
        email = _email("reset-strong")
        user_pk = _seed_user(django_db_blocker, email, password="OldPass123!")

        with django_db_blocker.unblock():
            from django.db import connection

            from accounts.views.auth import _generate_password_reset_token

            token = _generate_password_reset_token(user_pk)
            connection.close()

        page.goto(
            f"{django_server}/api/password-reset?token={token}",
            wait_until="domcontentloaded",
        )
        page.fill("#new-password", "ResetPass123!")
        page.fill("#confirm-password", "ResetPass123!")
        page.click("#reset-submit")

        success = page.locator("#reset-success")
        success.wait_for(state="visible")
        assert "Password has been reset successfully." in success.inner_text()
        expect(page.get_by_role("link", name="Back to Sign in")).to_be_visible()

        page.wait_for_url(f"{django_server}/accounts/login/", timeout=10000)
        page.fill("#login-email", email)
        page.fill("#login-password", "ResetPass123!")
        page.click("#login-submit")
        page.wait_for_url(f"{django_server}/", timeout=10000)
        expect(page.locator('[data-testid="account-menu-trigger"]')).to_be_visible()
