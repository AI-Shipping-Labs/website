"""Playwright E2E test for the consolidated /account/ page (issue #447).

Issue #447 folded the standalone ``/account/profile`` page back into
``/account/``: the Profile name form lives inline at the top of the
Account page, the legacy ``/account/profile`` URL ``301``s back to
``/account/``, and a member can now edit their name and change their
password without ever leaving ``/account/``.

This file replaces the issue-#439 single-flow test. It pins three
behaviours that justify a real-browser test:

1. The member edits their name AND their password in the same session
   without the URL leaving ``/account/`` (other than the brief PRG
   redirect to ``/account/#profile``).
2. A bookmark to the legacy ``/account/profile`` URL still lands the
   member on ``/account/`` via a single ``301`` redirect.
3. The mobile menu Profile link sends the member to ``/account/#profile``
   so the page scrolls straight to the name form.

Validation, redirect targets, and anonymous-access gating are pinned in
``accounts/tests/test_account_view.py`` (faster, more reliable in
``TestCase`` than spinning up a browser).

Usage:
    uv run pytest playwright_tests/test_account_profile.py -v
"""

import os

import pytest

from playwright_tests.conftest import (
    DEFAULT_PASSWORD,
    auth_context,
    create_user,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestScenarioMemberUpdatesNameAndPassword:
    """A free member edits their name and password without leaving /account/."""

    def test_name_and_password_in_same_session_on_account_page(
        self, django_server, django_db_blocker, browser
    ):
        with django_db_blocker.unblock():
            create_user("account-merge-e2e@test.com", tier_slug="free")

        new_password = "NewSecure456!"
        context = auth_context(browser, "account-merge-e2e@test.com")
        try:
            page = context.new_page()

            # 1. Land on /account/. The Profile card sits at the top of
            #    the cards stack and the inputs are empty (name not set).
            page.goto(
                f"{django_server}/account/",
                wait_until="domcontentloaded",
            )
            section = page.locator("#profile-section")
            assert section.is_visible()
            assert page.locator("#id_first_name").input_value() == ""
            assert page.locator("#id_last_name").input_value() == ""

            # 2. Fill the inline form and save.
            page.locator("#id_first_name").fill("Alice")
            page.locator("#id_last_name").fill("Doe")
            page.locator("#profile-save-btn").click()

            # PRG redirect lands at /account/#profile -- never leaves
            # the account surface, just adds an anchor for scroll.
            page.wait_for_url(f"{django_server}/account/#profile")

            assert page.locator("#id_first_name").input_value() == "Alice"
            assert page.locator("#id_last_name").input_value() == "Doe"
            assert (
                "Your profile has been updated."
                in page.locator("body").inner_text()
            )

            # 3. Stay on /account/ and change the password using the
            #    existing JS+JSON form on the same page.
            change_form = page.locator("#change-password-form")
            assert change_form.is_visible()
            page.fill("#current-password", DEFAULT_PASSWORD)
            page.fill("#new-password", new_password)
            page.fill("#confirm-new-password", new_password)
            page.click("#change-password-form button[type='submit']")

            success = page.locator("#password-success")
            success.wait_for(state="visible")
            assert "password" in success.inner_text().lower()
            # Inputs are reset after the JS handler runs.
            assert page.locator("#current-password").input_value() == ""
            assert page.locator("#new-password").input_value() == ""
            assert page.locator("#confirm-new-password").input_value() == ""

            # The user never left /account/ during the password change
            # (the JSON endpoint does not navigate). The URL still has
            # the #profile fragment from the earlier PRG redirect, but
            # the path is /account/.
            assert page.url.startswith(f"{django_server}/account/")

            # 4. Reload /account/. The saved name persisted across the
            #    password change.
            page.goto(
                f"{django_server}/account/",
                wait_until="domcontentloaded",
            )
            assert page.locator("#id_first_name").input_value() == "Alice"
            assert page.locator("#id_last_name").input_value() == "Doe"
        finally:
            context.close()


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestScenarioLegacyProfileBookmarkRedirects:
    """Bookmark to /account/profile still lands the member on /account/."""

    def test_legacy_url_permanently_redirects_to_account(
        self, django_server, django_db_blocker, browser
    ):
        with django_db_blocker.unblock():
            create_user("legacy-bookmark-e2e@test.com", tier_slug="free")

        context = auth_context(browser, "legacy-bookmark-e2e@test.com")
        try:
            page = context.new_page()

            # Visiting the legacy URL ends up at /account/ -- the browser
            # follows the 301 transparently, but we can confirm the final
            # landing URL and that the Profile card is on the page.
            response = page.goto(
                f"{django_server}/account/profile",
                wait_until="domcontentloaded",
            )

            assert page.url.rstrip("/") == f"{django_server}/account".rstrip("/")
            # The HTTP response chain must include a 301; ``response`` is
            # the final response, but its ``request.redirected_from`` chain
            # carries the original 301.
            chain = []
            current = response.request
            while current is not None:
                chain.append(current)
                current = current.redirected_from
            statuses = []
            for req in chain:
                resp = req.response()
                if resp is not None:
                    statuses.append(resp.status)
            assert 301 in statuses, (
                f"expected a 301 in the redirect chain, got {statuses}"
            )

            assert page.locator("#profile-section").is_visible()
        finally:
            context.close()


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestScenarioMobileMenuProfileLinkScrollsToForm:
    """Mobile menu Profile link sends the member to /account/#profile."""

    def test_mobile_menu_profile_link_targets_anchor(
        self, django_server, django_db_blocker, browser
    ):
        with django_db_blocker.unblock():
            create_user("mobile-profile-e2e@test.com", tier_slug="free")

        context = auth_context(browser, "mobile-profile-e2e@test.com")
        try:
            page = context.new_page()
            # 390 px viewport mirrors the spec scenario (mobile breakpoint
            # at which the mobile menu becomes the only nav surface).
            page.set_viewport_size({"width": 390, "height": 844})
            # Start on a non-account page so the mobile-menu Profile tap
            # actually triggers a navigation.
            page.goto(
                f"{django_server}/about",
                wait_until="domcontentloaded",
            )

            # Open the mobile menu; the Profile link is only visible
            # inside the slide-out drawer.
            page.locator("#mobile-menu-btn").click()
            mobile_link = page.locator("#mobile-profile-link")
            mobile_link.wait_for(state="visible")
            assert mobile_link.get_attribute("href") == "/account/#profile"

            mobile_link.click()
            page.wait_for_url(f"{django_server}/account/#profile")

            # The Profile card with the name form is present on the
            # destination page (the anchor is what the spec calls out).
            section = page.locator("#profile-section")
            section.wait_for(state="visible")
            assert section.is_visible()
        finally:
            context.close()
