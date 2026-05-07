"""
Playwright E2E tests for the dark/light theme toggle (issue #267).

Covers two flows the Django unit tests can't reach:

1. Anonymous user clicks the toggle on the homepage; the dark class is
   applied to <html>, written to localStorage, and survives a full
   navigation to /blog (read back on the next page-load).
2. A logged-in user with theme_preference='dark' lands on the homepage
   with the dark class already on <html> (no flash of light theme), then
   toggling to light and reloading shows the preference round-tripped
   through the backend (/api/account/theme-preference) and re-synced to
   localStorage on the next login.

Backend persistence and the /api/account/theme-preference endpoint are
covered by accounts/tests/test_theme.py; this file only verifies the
browser-level UI path (clicks, classes, localStorage, first-paint).

Usage:
    uv run pytest playwright_tests/test_theme_toggle.py -v
"""

import os

import pytest

from playwright_tests.conftest import (
    DEFAULT_PASSWORD,
    VIEWPORT,
)

# Playwright's internal event loop trips Django's async safety check
# whenever helper code touches the ORM; opt out the same way the rest of
# the playwright_tests/ suite does.
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _create_dark_user(email="theme-dark@test.com"):
    """Create a verified user with theme_preference='dark'.

    Returns the user. Closes the DB connection so the server thread can
    read the row without contending for SQLite locks.
    """
    from django.db import connection

    from accounts.models import User
    from playwright_tests.conftest import ensure_tiers

    ensure_tiers()
    user, _ = User.objects.get_or_create(
        email=email,
        defaults={"email_verified": True},
    )
    user.set_password(DEFAULT_PASSWORD)
    user.email_verified = True
    user.theme_preference = "dark"
    user.save()
    connection.close()
    return user


def _html_has_dark_class(page):
    """Read the <html> element classList and check for 'dark'."""
    return page.evaluate(
        "() => document.documentElement.classList.contains('dark')"
    )


def _read_local_storage_theme(page):
    """Return the value of localStorage['theme'] (or None)."""
    return page.evaluate("() => localStorage.getItem('theme')")


def _click_visible_theme_toggle(page):
    """Click the theme toggle in the UI path visible for this viewport."""
    account_trigger = page.locator("#account-menu-trigger")
    if account_trigger.count() and account_trigger.is_visible():
        account_trigger.click()
        account_menu = page.locator("#account-menu-dropdown")
        account_menu.wait_for(state="visible", timeout=2000)
        account_menu.locator('[data-testid="theme-toggle"]').click()
        return

    page.locator('[data-testid="theme-toggle"]:visible').first.click()


# ---------------------------------------------------------------------------
# Scenario 1: anonymous user toggles theme and it survives navigation
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestAnonymousThemeToggle:
    """An anonymous visitor can flip to dark mode and the choice is
    remembered as they browse other pages."""

    def test_default_is_light_then_toggle_persists_across_navigation(
        self, django_server, page
    ):
        # Force light mode regardless of OS-level prefers-color-scheme so
        # the assertion about the default state is deterministic. The
        # blocking script in base.html falls back to that media query
        # when localStorage is empty.
        context = page.context
        context.add_init_script(
            "window.matchMedia = function(q) {"
            "  return {"
            "    matches: false,"
            "    media: q,"
            "    addListener: function() {},"
            "    removeListener: function() {},"
            "    addEventListener: function() {},"
            "    removeEventListener: function() {},"
            "    dispatchEvent: function() { return false; }"
            "  };"
            "};"
        )

        # ---- Initial paint on / : light theme by default ----
        page.goto(f"{django_server}/", wait_until="domcontentloaded")
        assert _html_has_dark_class(page) is False, (
            "anonymous visitor should land on / in light mode by default"
        )
        # localStorage is untouched until the user actually clicks.
        assert _read_local_storage_theme(page) is None

        # ---- Click the (desktop) theme toggle ----
        # Both the desktop and mobile menus render a button with the
        # same data-testid. At our 1280px viewport only the desktop one
        # is visible, so .first targets it deterministically.
        _click_visible_theme_toggle(page)

        assert _html_has_dark_class(page) is True, (
            "clicking the toggle should add 'dark' to <html>"
        )
        assert _read_local_storage_theme(page) == "dark", (
            "toggling should write 'dark' to localStorage so other "
            "tabs / pages can read it"
        )

        # ---- Navigate to /blog : dark mode must persist ----
        page.goto(f"{django_server}/blog", wait_until="domcontentloaded")
        assert _html_has_dark_class(page) is True, (
            "after a full navigation to /blog the <html> element should "
            "still carry the 'dark' class (read back from localStorage "
            "by the blocking script in base.html)"
        )
        assert _read_local_storage_theme(page) == "dark"


# ---------------------------------------------------------------------------
# Scenario 2: logged-in user's stored preference is honored on first paint
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestLoggedInThemePreference:
    """A user whose account has theme_preference='dark' should see dark
    mode immediately on first paint and have toggle changes round-trip
    through the backend so future logins remember them."""

    def _login_via_form(self, page, base_url, email, password):
        """Submit the real /accounts/login/ form so the post-login JS
        copies user.theme_preference into localStorage (the same path
        as a real browser session)."""
        page.goto(
            f"{base_url}/accounts/login/", wait_until="domcontentloaded"
        )
        page.fill("#login-email", email)
        page.fill("#login-password", password)
        # The login form uses a fetch() POST and only redirects to /
        # on success, so wait for the URL change.
        with page.expect_navigation(
            url=f"{base_url}/", wait_until="domcontentloaded"
        ):
            page.click("#login-submit")

    def test_dark_preference_renders_on_first_paint(
        self, django_server, browser, django_db_blocker
    ):
        """After logging in as a user with theme_preference='dark', the
        very first paint of / has the dark class on <html> -- no flash
        of light theme."""
        with django_db_blocker.unblock():
            _create_dark_user("theme-dark@test.com")

        context = browser.new_context(viewport=VIEWPORT)
        page = context.new_page()
        try:
            self._login_via_form(
                page, django_server, "theme-dark@test.com", DEFAULT_PASSWORD
            )

            # After the navigation triggered by login, we are on /.
            assert page.url.rstrip("/") == django_server.rstrip("/")
            assert _html_has_dark_class(page) is True, (
                "user with theme_preference='dark' should see dark mode "
                "on the very first paint after login (no flash)"
            )
            assert _read_local_storage_theme(page) == "dark", (
                "login should sync the server-side theme preference "
                "into localStorage so subsequent navigations stay dark"
            )
        finally:
            context.close()

    def test_toggling_to_light_persists_across_reload(
        self, django_server, browser, django_db_blocker
    ):
        """Logged-in user toggles to light, reloads, and the preference
        is honored on the next paint (round-tripped through the backend
        and re-applied via localStorage)."""
        with django_db_blocker.unblock():
            _create_dark_user("theme-dark2@test.com")

        context = browser.new_context(viewport=VIEWPORT)
        page = context.new_page()
        try:
            self._login_via_form(
                page,
                django_server,
                "theme-dark2@test.com",
                DEFAULT_PASSWORD,
            )

            # Sanity: dark on first paint.
            assert _html_has_dark_class(page) is True

            # Capture the theme-preference POST so we know the toggle
            # actually called the backend before the reload (otherwise
            # we'd be testing localStorage persistence only, not the
            # backend round-trip).
            with page.expect_response(
                lambda r: (
                    r.url.endswith("/api/account/theme-preference")
                    and r.request.method == "POST"
                ),
                timeout=3000,
            ) as resp_info:
                _click_visible_theme_toggle(page)
            resp = resp_info.value
            assert resp.status == 200, (
                f"theme-preference POST should return 200, got "
                f"{resp.status}"
            )

            # Toggle worked client-side too.
            assert _html_has_dark_class(page) is False
            assert _read_local_storage_theme(page) == "light"

            # Reload: the saved-to-backend preference (light) should be
            # honored. We clear localStorage first to prove the value
            # really came back from the server via the next login --
            # but a plain reload of an authenticated session does NOT
            # re-run /api/login, so the source of truth on reload is
            # localStorage. Verify both: localStorage survived the
            # reload AND the visible state is light.
            page.reload(wait_until="domcontentloaded")
            assert _html_has_dark_class(page) is False, (
                "after toggling to light and reloading, <html> should "
                "no longer carry the 'dark' class"
            )
            assert _read_local_storage_theme(page) == "light"

            # And the backend now stores 'light': verify by clearing
            # localStorage, logging out, and logging back in -- which
            # re-runs the /api/login JS that re-seeds localStorage from
            # the user's saved theme_preference.
            page.evaluate("() => localStorage.clear()")
            page.goto(
                f"{django_server}/accounts/logout/",
                wait_until="domcontentloaded",
            )
            self._login_via_form(
                page,
                django_server,
                "theme-dark2@test.com",
                DEFAULT_PASSWORD,
            )
            assert _read_local_storage_theme(page) == "light", (
                "after logging back in, localStorage should be re-seeded "
                "from the user's server-side theme_preference (which is "
                "now 'light' after the toggle round-trip)"
            )
            assert _html_has_dark_class(page) is False
        finally:
            context.close()
