"""Playwright coverage for login submit feedback (issue #371)."""

import os

import pytest

from playwright_tests.conftest import DEFAULT_PASSWORD, create_user

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")


def _seed_user(db_blocker, email, **kwargs):
    with db_blocker.unblock():
        return create_user(email=email, password=DEFAULT_PASSWORD, **kwargs)


def _open_login(page, base_url, email, password=DEFAULT_PASSWORD):
    page.goto(f"{base_url}/accounts/login/", wait_until="domcontentloaded")
    page.fill("#login-email", email)
    page.fill("#login-password", password)


def _delay_login_fetch(page, delay_ms=500):
    script = """
        (() => {
          const delayMs = __DELAY_MS__;
          if (window.loginFetchDelayInstalled) {
            return;
          }
          window.loginFetchDelayInstalled = true;
          window.loginFetchCount = 0;
          const nativeFetch = window.fetch.bind(window);
          window.fetch = (input, init) => {
            const url = typeof input === 'string' ? input : input.url;
            if (url === '/api/login' || url.endsWith('/api/login')) {
              window.loginFetchCount += 1;
              return new Promise((resolve, reject) => {
                setTimeout(() => nativeFetch(input, init).then(resolve, reject), delayMs);
              });
            }
            return nativeFetch(input, init);
          };
        })()
        """.replace("__DELAY_MS__", str(delay_ms))
    page.add_init_script(script)
    page.evaluate(script)


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestLoginSubmitFeedback:
    def test_valid_login_shows_immediate_busy_disabled_state(
        self, django_server, page, django_db_blocker
    ):
        _seed_user(django_db_blocker, "login-feedback@test.com")
        _delay_login_fetch(page)
        _open_login(page, django_server, "login-feedback@test.com")

        page.click("#login-submit")

        submit = page.locator("#login-submit")
        assert submit.inner_text() == "Signing in..."
        assert submit.is_disabled()
        assert submit.get_attribute("aria-busy") == "true"
        assert page.locator("#login-form").get_attribute("aria-busy") == "true"

        page.wait_for_url(f"{django_server}/", timeout=10000)

    def test_double_submit_is_prevented_while_request_is_pending(
        self, django_server, page, django_db_blocker
    ):
        _seed_user(django_db_blocker, "login-double-submit@test.com")
        _delay_login_fetch(page)
        _open_login(page, django_server, "login-double-submit@test.com")

        page.click("#login-submit")
        page.evaluate(
            """
            () => {
              document.getElementById('login-form').dispatchEvent(
                new Event('submit', { bubbles: true, cancelable: true })
              );
            }
            """
        )

        assert page.evaluate("window.loginFetchCount") == 1
        assert page.locator("#login-submit").is_disabled()
        page.wait_for_url(f"{django_server}/", timeout=10000)

    def test_invalid_credentials_restore_idle_state_and_clear_on_retry(
        self, django_server, page, django_db_blocker
    ):
        _seed_user(django_db_blocker, "login-wrong-password@test.com")
        _open_login(
            page,
            django_server,
            "login-wrong-password@test.com",
            password="wrongpass",
        )

        page.click("#login-submit")
        error = page.locator("#login-error")
        error.wait_for(state="visible")
        assert error.inner_text() == "Invalid email or password"
        assert page.locator("#login-submit").inner_text() == "Sign in"
        assert not page.locator("#login-submit").is_disabled()

        page.fill("#login-password", DEFAULT_PASSWORD)
        _delay_login_fetch(page)
        page.click("#login-submit")
        assert error.inner_text() == ""
        assert error.is_hidden()
        assert page.locator("#login-submit").inner_text() == "Signing in..."
        page.wait_for_url(f"{django_server}/", timeout=10000)

    def test_unknown_email_uses_same_error_and_restores_idle_state(
        self, django_server, page
    ):
        _open_login(page, django_server, "unknown-login@test.com")

        page.click("#login-submit")

        error = page.locator("#login-error")
        error.wait_for(state="visible")
        assert error.inner_text() == "Invalid email or password"
        assert page.locator("#login-submit").inner_text() == "Sign in"
        assert not page.locator("#login-submit").is_disabled()

    def test_transient_network_failure_can_retry(
        self, django_server, page, django_db_blocker
    ):
        _seed_user(django_db_blocker, "login-network-retry@test.com")
        seen = {"count": 0}

        def fail_once(route):
            seen["count"] += 1
            if seen["count"] == 1:
                route.abort()
            else:
                route.continue_()

        page.route("**/api/login", fail_once)
        _open_login(page, django_server, "login-network-retry@test.com")

        page.click("#login-submit")
        error = page.locator("#login-error")
        error.wait_for(state="visible")
        assert error.inner_text() == "An error occurred. Please try again."
        assert not page.locator("#login-submit").is_disabled()

        page.click("#login-submit")
        page.wait_for_url(f"{django_server}/", timeout=10000)
