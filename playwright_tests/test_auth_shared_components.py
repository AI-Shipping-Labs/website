"""Playwright coverage for shared login/register auth components (issue #386)."""

import os
import uuid

import pytest

from playwright_tests.conftest import DEFAULT_PASSWORD, create_user, ensure_tiers

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")


def _seed_user(db_blocker, email, **kwargs):
    with db_blocker.unblock():
        return create_user(email=email, password=DEFAULT_PASSWORD, **kwargs)


def _configure_oauth(db_blocker, *providers):
    with db_blocker.unblock():
        from allauth.socialaccount.models import SocialApp
        from django.contrib.sites.models import Site
        from django.db import connection

        SocialApp.objects.all().delete()
        site = Site.objects.get_current()
        names = {
            "google": "Google",
            "github": "GitHub",
            "slack": "Slack",
        }
        for provider in providers:
            app = SocialApp.objects.create(
                provider=provider,
                name=names[provider],
                client_id=f"{provider}-cid",
                secret=f"{provider}-secret",
            )
            app.sites.add(site)
        connection.close()


def _new_email(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:8]}@test.com"


def _ensure_tiers(db_blocker):
    with db_blocker.unblock():
        ensure_tiers()


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestSharedAuthJourneys:
    def test_login_success_shows_immediate_feedback(
        self, django_server, page, django_db_blocker
    ):
        email = _new_email("login-feedback-386")
        _seed_user(django_db_blocker, email)
        script = """
            (() => {
              const nativeFetch = window.fetch.bind(window);
              window.fetch = (input, init) => {
                const url = typeof input === 'string' ? input : input.url;
                if (url === '/api/login' || url.endsWith('/api/login')) {
                  return new Promise((resolve, reject) => {
                    setTimeout(() => nativeFetch(input, init).then(resolve, reject), 500);
                  });
                }
                return nativeFetch(input, init);
              };
            })()
        """
        page.add_init_script(script)
        page.goto(f"{django_server}/accounts/login/", wait_until="domcontentloaded")
        page.fill("#login-email", email)
        page.fill("#login-password", DEFAULT_PASSWORD)

        page.click("#login-submit")

        assert page.locator("#login-submit").inner_text() == "Signing in..."
        assert page.locator("#login-submit").is_disabled()
        assert page.locator("#login-submit").get_attribute("aria-busy") == "true"
        assert page.locator("#login-form").get_attribute("aria-busy") == "true"
        page.wait_for_url(f"{django_server}/", timeout=10000)

    def test_login_invalid_password_can_retry(
        self, django_server, page, django_db_blocker
    ):
        email = _new_email("login-retry-386")
        _seed_user(django_db_blocker, email)
        page.goto(f"{django_server}/accounts/login/", wait_until="domcontentloaded")
        page.fill("#login-email", email)
        page.fill("#login-password", "wrongpass")

        page.click("#login-submit")

        error = page.locator("#login-error")
        error.wait_for(state="visible")
        assert error.inner_text() == "Invalid email or password"
        assert page.locator("#login-submit").inner_text() == "Sign in"
        assert not page.locator("#login-submit").is_disabled()

        page.fill("#login-password", DEFAULT_PASSWORD)
        page.click("#login-submit")

        page.wait_for_url(f"{django_server}/", timeout=10000)

    def test_register_success_resets_form_and_creates_free_user(
        self, django_server, page, django_db_blocker
    ):
        _ensure_tiers(django_db_blocker)
        email = _new_email("new-free-386")
        page.goto(f"{django_server}/accounts/register/", wait_until="domcontentloaded")
        page.fill("#register-email", email)
        page.fill("#register-password", DEFAULT_PASSWORD)
        page.fill("#register-password-confirm", DEFAULT_PASSWORD)

        page.click("#register-submit")

        success = page.locator("#register-success")
        success.wait_for(state="visible")
        assert "Account created. Check your email" in success.inner_text()
        assert page.locator("#register-email").input_value() == ""
        assert page.locator("#register-password").input_value() == ""
        assert page.locator("#register-password-confirm").input_value() == ""
        with django_db_blocker.unblock():
            from accounts.models import User

            user = User.objects.get(email=email)
            assert user.tier.slug == "free"

    def test_register_password_mismatch_does_not_call_api_then_can_retry(
        self, django_server, page
    ):
        email = _new_email("register-mismatch-386")
        register_calls = {"count": 0}

        def count_register(route):
            register_calls["count"] += 1
            route.continue_()

        page.route("**/api/register", count_register)
        page.goto(f"{django_server}/accounts/register/", wait_until="domcontentloaded")
        page.fill("#register-email", email)
        page.fill("#register-password", DEFAULT_PASSWORD)
        page.fill("#register-password-confirm", "Different123!")

        page.click("#register-submit")

        error = page.locator("#register-error")
        error.wait_for(state="visible")
        assert error.inner_text() == "Passwords do not match"
        assert register_calls["count"] == 0

        page.fill("#register-password-confirm", DEFAULT_PASSWORD)
        page.click("#register-submit")

        page.locator("#register-success").wait_for(state="visible")
        assert register_calls["count"] == 1

    def test_register_duplicate_email_shows_api_error(
        self, django_server, page, django_db_blocker
    ):
        email = _new_email("register-duplicate-386")
        _seed_user(django_db_blocker, email)
        page.goto(f"{django_server}/accounts/register/", wait_until="domcontentloaded")
        page.fill("#register-email", email)
        page.fill("#register-password", DEFAULT_PASSWORD)
        page.fill("#register-password-confirm", DEFAULT_PASSWORD)

        page.click("#register-submit")

        error = page.locator("#register-error")
        error.wait_for(state="visible")
        assert error.inner_text() == "A user with this email already exists"
        assert page.locator("#register-success").is_hidden()

    def test_login_shows_only_enabled_oauth_provider(
        self, django_server, page, django_db_blocker
    ):
        _configure_oauth(django_db_blocker, "google")

        page.goto(f"{django_server}/accounts/login/", wait_until="domcontentloaded")

        assert page.locator("[data-auth-oauth-divider]").is_visible()
        google = page.get_by_role("link", name="Sign in with Google")
        assert google.is_visible()
        assert google.get_attribute("href").endswith("/accounts/google/login/")
        assert page.get_by_role("link", name="Sign in with GitHub").count() == 0
        assert page.get_by_role("link", name="Sign in with Slack").count() == 0

    def test_register_shows_only_enabled_oauth_provider(
        self, django_server, page, django_db_blocker
    ):
        _configure_oauth(django_db_blocker, "slack")

        page.goto(f"{django_server}/accounts/register/", wait_until="domcontentloaded")

        assert page.locator("[data-auth-oauth-divider]").is_visible()
        slack = page.get_by_role("link", name="Sign up with Slack")
        assert slack.is_visible()
        assert slack.get_attribute("href").endswith("/accounts/slack/login/")
        assert page.get_by_role("link", name="Sign up with Google").count() == 0
        assert page.get_by_role("link", name="Sign up with GitHub").count() == 0

    def test_email_only_auth_hides_oauth_on_both_pages(
        self, django_server, page, django_db_blocker
    ):
        _configure_oauth(django_db_blocker)

        page.goto(f"{django_server}/accounts/login/", wait_until="domcontentloaded")
        assert page.locator("#login-form").is_visible()
        assert page.locator("[data-auth-oauth-divider]").count() == 0
        assert page.get_by_role("link", name="Sign in with Google").count() == 0
        assert page.get_by_role("link", name="Sign in with GitHub").count() == 0
        assert page.get_by_role("link", name="Sign in with Slack").count() == 0

        page.goto(f"{django_server}/accounts/register/", wait_until="domcontentloaded")
        assert page.locator("#register-form").is_visible()
        assert page.locator("[data-auth-oauth-divider]").count() == 0
        assert page.get_by_role("link", name="Sign up with Google").count() == 0
        assert page.get_by_role("link", name="Sign up with GitHub").count() == 0
        assert page.get_by_role("link", name="Sign up with Slack").count() == 0

    def test_login_register_navigation_keeps_legal_links(
        self, django_server, page
    ):
        page.goto(f"{django_server}/accounts/login/", wait_until="domcontentloaded")

        page.click("#register-link")
        page.wait_for_url(f"{django_server}/accounts/register/")
        assert page.locator("#register-form").is_visible()
        assert page.locator("section").locator('a[href="/terms/"]').is_visible()
        assert page.locator("section").locator('a[href="/privacy/"]').is_visible()

        page.click("#login-link")
        page.wait_for_url(f"{django_server}/accounts/login/")
        assert page.locator("#login-form").is_visible()
        assert page.locator("section").locator('a[href="/terms/"]').is_visible()
        assert page.locator("section").locator('a[href="/privacy/"]').is_visible()
