"""Playwright E2E test for the member profile page (issue #439).

Single scenario: a Free member with no name set discovers the Profile
form from the Account page, fills it in, saves, and the new name
round-trips back to the Account page card. This exercises the discovery
path, the form interaction, persistence, and the post-redirect render.
Other behaviours (validation errors, anonymous redirects, API
round-trip) are faster and more reliable in Django ``TestCase``.

Usage:
    uv run pytest playwright_tests/test_account_profile.py -v
"""

import os

import pytest

from playwright_tests.conftest import (
    auth_context,
    create_user,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")


@pytest.mark.django_db(transaction=True)
class TestScenarioMemberSetsTheirName:
    """A free member sets their name from the Account page Profile section."""

    def test_member_sets_name_from_account_page(
        self, django_server, django_db_blocker, browser
    ):
        with django_db_blocker.unblock():
            create_user("profile-e2e@test.com", tier_slug="free")

        context = auth_context(browser, "profile-e2e@test.com")
        try:
            page = context.new_page()

            # 1. Land on /account/ and verify the Profile section invites
            #    the member to set their name (since none is set yet).
            page.goto(
                f"{django_server}/account/",
                wait_until="domcontentloaded",
            )
            section = page.locator("#profile-section")
            assert section.is_visible()
            assert "Your name is not set yet" in section.inner_text()

            # 2. Click "Edit profile" -> /account/profile.
            page.locator("#edit-profile-link").click()
            page.wait_for_url(f"{django_server}/account/profile")

            first_input = page.locator("#id_first_name")
            last_input = page.locator("#id_last_name")
            assert first_input.input_value() == ""
            assert last_input.input_value() == ""

            # 3. Type the name and save.
            first_input.fill("Alice")
            last_input.fill("Doe")
            page.locator("#profile-save-btn").click()
            page.wait_for_url(f"{django_server}/account/profile")

            # 4. After the PRG redirect, inputs are pre-filled and the
            #    success flash is on the page.
            assert page.locator("#id_first_name").input_value() == "Alice"
            assert page.locator("#id_last_name").input_value() == "Doe"
            assert (
                "Your profile has been updated."
                in page.locator("body").inner_text()
            )

            # 5. Navigate back to /account/ -- the Profile card now shows
            #    the saved name and still links to the editor.
            page.goto(
                f"{django_server}/account/",
                wait_until="domcontentloaded",
            )
            assert (
                "Alice Doe"
                in page.locator("#profile-current-name").inner_text()
            )
            edit_link = page.locator("#edit-profile-link")
            assert edit_link.get_attribute("href") == "/account/profile"
        finally:
            context.close()
