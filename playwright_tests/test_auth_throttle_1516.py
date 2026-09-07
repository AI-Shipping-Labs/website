"""Visible 429 copy on public auth and mail forms (issue #1516)."""

import os
import uuid

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import DEFAULT_PASSWORD, create_user
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = [pytest.mark.local_only, pytest.mark.django_db(transaction=True)]

THROTTLED_COPY = "Too many attempts. Please try again later."
INVALID_LOGIN = "Invalid email or password"
RESET_SUCCESS = (
    "If an account exists for that email, we’ll send password reset "
    "instructions shortly."
)
SUBSCRIBE_SUCCESS_SNIPPET = "free account"


def _email(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:8]}@example.com"


def _set_throttle_limits(db_blocker, **values):
    with db_blocker.unblock():
        from django.db import connection

        from accounts.services.auth_throttle import clear_auth_throttle_cache
        from integrations.config import clear_config_cache
        from integrations.models import IntegrationSetting

        for key, value in values.items():
            IntegrationSetting.objects.update_or_create(
                key=key,
                defaults={"value": str(value)},
            )
        clear_config_cache()
        clear_auth_throttle_cache()
        connection.close()


def _seed_user(db_blocker, email, password=DEFAULT_PASSWORD):
    with db_blocker.unblock():
        create_user(email=email, password=password)


def _log_out(page):
    page.locator('[data-testid="account-menu-trigger"]').click()
    page.get_by_role("menuitem", name="Log out").click()
    expect(page.locator('[data-testid="account-menu-trigger"]')).to_have_count(0)


class TestAuthThrottleJourneys:
    @browser_journey
    def test_visitor_who_mistypes_password_is_asked_to_wait(
        self, django_server, page, django_db_blocker
    ):
        email = "free@test.com"
        _seed_user(django_db_blocker, email)
        _set_throttle_limits(
            django_db_blocker,
            AUTH_THROTTLE_LOGIN_IP_LIMIT=2,
            AUTH_THROTTLE_LOGIN_EMAIL_LIMIT=2,
            AUTH_THROTTLE_LOGIN_WINDOW_SECONDS=900,
        )
        page.goto(
            f"{django_server}/accounts/login/", wait_until="domcontentloaded"
        )
        error = page.locator("#login-error")

        page.fill("#login-email", email)
        page.fill("#login-password", "wrong-password")
        page.click("#login-submit")
        error.wait_for(state="visible")
        expect(error).to_have_text(INVALID_LOGIN)

        page.click("#login-submit")
        error.wait_for(state="visible")
        expect(error).to_have_text(INVALID_LOGIN)

        page.fill("#login-password", DEFAULT_PASSWORD)
        page.click("#login-submit")
        error.wait_for(state="visible")
        expect(error).to_have_text(THROTTLED_COPY)
        expect(page.locator('[data-testid="account-menu-trigger"]')).to_have_count(
            0
        )
        assert page.url.startswith(f"{django_server}/accounts/login/")

    @browser_journey
    def test_throttled_login_does_not_block_first_newsletter_subscribe(
        self, django_server, page, django_db_blocker
    ):
        email = "free@test.com"
        _seed_user(django_db_blocker, email)
        _set_throttle_limits(
            django_db_blocker,
            AUTH_THROTTLE_LOGIN_IP_LIMIT=1,
            AUTH_THROTTLE_LOGIN_EMAIL_LIMIT=1,
            AUTH_THROTTLE_LOGIN_WINDOW_SECONDS=900,
        )
        page.goto(
            f"{django_server}/accounts/login/", wait_until="domcontentloaded"
        )
        page.fill("#login-email", email)
        page.fill("#login-password", "wrong-password")
        page.click("#login-submit")
        expect(page.locator("#login-error")).to_have_text(INVALID_LOGIN)
        page.click("#login-submit")
        expect(page.locator("#login-error")).to_have_text(THROTTLED_COPY)

        page.goto(f"{django_server}/subscribe", wait_until="domcontentloaded")
        form = page.locator(".subscribe-form-container").first
        form.locator('input[name="email"]').fill("throttle-sub@example.com")
        form.locator('button[type="submit"]').click()
        message = form.locator(".subscribe-message")
        message.wait_for(state="visible")
        assert SUBSCRIBE_SUCCESS_SNIPPET in message.inner_text().lower()
        expect(form.locator(".subscribe-error")).to_be_hidden()
        assert THROTTLED_COPY not in form.inner_text()

    @browser_journey
    def test_visitor_who_hammers_account_creation_is_asked_to_wait(
        self, django_server, page, django_db_blocker
    ):
        _set_throttle_limits(
            django_db_blocker,
            AUTH_THROTTLE_MAIL_IP_LIMIT=1,
            AUTH_THROTTLE_MAIL_EMAIL_LIMIT=1,
            AUTH_THROTTLE_MAIL_WINDOW_SECONDS=3600,
        )
        first_email = _email("throttle-reg-one")
        page.goto(
            f"{django_server}/accounts/register/", wait_until="domcontentloaded"
        )
        page.fill("#register-email", first_email)
        page.fill("#register-password", DEFAULT_PASSWORD)
        page.fill("#register-password-confirm", DEFAULT_PASSWORD)
        page.click("#register-submit")
        page.wait_for_url(f"{django_server}/", timeout=10000)
        expect(page.locator('[data-testid="account-menu-trigger"]')).to_be_visible()
        _log_out(page)

        page.goto(
            f"{django_server}/accounts/register/", wait_until="domcontentloaded"
        )
        page.fill("#register-email", _email("throttle-reg-two"))
        page.fill("#register-password", DEFAULT_PASSWORD)
        page.fill("#register-password-confirm", DEFAULT_PASSWORD)
        page.click("#register-submit")
        error = page.locator("#register-error")
        error.wait_for(state="visible")
        expect(error).to_have_text(THROTTLED_COPY)
        expect(page.locator('[data-testid="account-menu-trigger"]')).to_have_count(
            0
        )
        assert page.url.startswith(f"{django_server}/accounts/register/")

    @browser_journey
    def test_reset_request_spam_is_told_to_wait_without_fake_success(
        self, django_server, page, django_db_blocker
    ):
        email = "free@test.com"
        _seed_user(django_db_blocker, email)
        _set_throttle_limits(
            django_db_blocker,
            AUTH_THROTTLE_MAIL_EMAIL_LIMIT=1,
            AUTH_THROTTLE_MAIL_IP_LIMIT=8,
            AUTH_THROTTLE_MAIL_WINDOW_SECONDS=3600,
        )
        page.goto(
            f"{django_server}/accounts/password-reset-request",
            wait_until="domcontentloaded",
        )
        page.fill("#password-reset-email", email)
        page.click("#password-reset-request-submit")
        success = page.locator("#password-reset-request-success")
        success.wait_for(state="visible")
        expect(success).to_have_text(RESET_SUCCESS)

        page.click("#password-reset-request-submit")
        error = page.locator("#password-reset-request-error")
        error.wait_for(state="visible")
        expect(error).to_have_text(THROTTLED_COPY)
        expect(success).to_be_hidden()
