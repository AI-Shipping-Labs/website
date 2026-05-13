"""Playwright E2E test for the Studio contact-tags flow (issue #354).

Single scenario covering the full operator loop:
1. Open the Studio users list.
2. Click "View" on a user to reach the new detail page.
3. Add a tag with a non-normalized label and confirm it shows up
   normalized.
4. Filter the users list by ?tag=<slug> and confirm the user appears
   plus an "active filter" chip.
5. Clear the filter via the chip's x and confirm the URL drops the
   ``tag=`` param.
6. Remove the tag from the detail page and confirm the user disappears
   from the filtered list.

Usage:
    uv run pytest playwright_tests/test_studio_user_tags.py -v
"""

import os

import pytest

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_staff_user as _create_staff_user,
)
from playwright_tests.conftest import (
    create_user as _create_user,
)
from playwright_tests.conftest import (
    ensure_tiers as _ensure_tiers,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection


def _clear_users_except_staff(staff_email):
    """Drop every user except the named staff account so the listing is
    deterministic for the filter assertions."""
    from accounts.models import User

    User.objects.exclude(email=staff_email).delete()
    connection.close()


@pytest.mark.django_db(transaction=True)
class TestOperatorTagsContactAndFiltersByTag:
    """Operator tags a contact and finds them via the tag filter."""

    @pytest.mark.core
    def test_full_tag_lifecycle(self, django_server, browser):
        _ensure_tiers()
        staff_email = "tag-admin@test.com"
        _create_staff_user(staff_email)
        _clear_users_except_staff(staff_email)
        _create_user("member@test.com", tier_slug="free")

        context = _auth_context(browser, staff_email)
        page = context.new_page()

        # 1. Navigate to /studio/users/
        page.goto(
            f"{django_server}/studio/users/",
            wait_until="domcontentloaded",
        )
        assert "/studio/users/" in page.url
        # The new Tags column header is present.
        assert "Tags" in page.content()

        # 2. Click "View" on the row for member@test.com.
        member_row = page.locator("tr", has_text="member@test.com")
        assert member_row.count() == 1
        member_row.locator('[data-testid="user-view-link"]').click()
        page.wait_for_load_state("domcontentloaded")

        # Detail page: email + empty Tags section.
        assert "/studio/users/" in page.url
        assert page.locator('[data-testid="user-detail-email"]').inner_text() == "member@test.com"
        assert page.locator('[data-testid="user-tags-empty"]').count() == 1

        # 3. Type "Early Adopter" in the tag input and submit.
        page.locator('[data-testid="user-tag-input"]').fill("Early Adopter")
        page.locator('[data-testid="user-tag-add-submit"]').click()
        page.wait_for_load_state("domcontentloaded")

        # The chip appears, normalized.
        chip = page.locator('[data-testid="user-tag-chip"][data-tag="early-adopter"]')
        assert chip.count() == 1
        assert "early-adopter" in chip.inner_text()

        # 4. Visit /studio/users/?tag=early-adopter
        page.goto(
            f"{django_server}/studio/users/?tag=early-adopter",
            wait_until="domcontentloaded",
        )
        body = page.content()
        assert "member@test.com" in body
        # Active filter chip is shown with the tag name.
        active_chip = page.locator('[data-testid="active-tag-chip"]')
        assert active_chip.count() == 1
        assert "Tag: early-adopter" in active_chip.inner_text()

        # 5. Click the x on the active filter chip.
        page.locator('[data-testid="active-tag-clear"]').click()
        page.wait_for_load_state("domcontentloaded")
        assert "tag=" not in page.url
        # The unfiltered list still shows member@test.com.
        assert "member@test.com" in page.content()

        # 6. Return to the user detail page and click the chip's x to remove
        # the tag.
        from accounts.models import User

        member_pk = User.objects.get(email="member@test.com").pk
        connection.close()

        page.goto(
            f"{django_server}/studio/users/{member_pk}/",
            wait_until="domcontentloaded",
        )
        page.locator('[data-testid="user-tag-chip"][data-tag="early-adopter"] [data-testid="user-tag-remove"]').click()
        page.wait_for_load_state("domcontentloaded")
        assert page.locator('[data-testid="user-tag-chip"][data-tag="early-adopter"]').count() == 0
        assert page.locator('[data-testid="user-tags-empty"]').count() == 1

        # And /studio/users/?tag=early-adopter no longer lists this user.
        page.goto(
            f"{django_server}/studio/users/?tag=early-adopter",
            wait_until="domcontentloaded",
        )
        assert "member@test.com" not in page.content()

        context.close()
