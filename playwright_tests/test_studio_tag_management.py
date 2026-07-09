"""Playwright E2E tests for the global tag-management surface (issue #694).

Covers the eight scenarios from the spec:

- Operator narrows the user list to one tag via the picker.
- Tag filter survives switching tier chips.
- Operator renames a tag and the change propagates to every user.
- Renaming to an empty value is rejected with a flash error.
- Rename collapses duplicates instead of creating them.
- Operator deletes a tag and confirms the impact across users.
- Non-staff cannot rename or delete tags.
- Per-user remove is separate from delete-everywhere.

Usage:
    uv run pytest playwright_tests/test_studio_tag_management.py -v

The fixture now picks a free OS-assigned port per session, so concurrent
runs from separate worktrees no longer collide. Set
``PLAYWRIGHT_DJANGO_PORT`` only if you need to pin a known port.
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
from django.db import connection  # noqa: E402

# Issue #656: this module uses local-only fixtures (DB seeding,
# session-cookie injection, etc.) and cannot run against the
# deployed dev environment. See _docs/testing-guidelines.md.
pytestmark = pytest.mark.local_only


def _clear_users_except_staff(staff_email):
    from accounts.models import User

    User.objects.exclude(email=staff_email).delete()
    connection.close()


def _set_tags(email, tags):
    from accounts.models import User

    user = User.objects.get(email=email)
    user.tags = tags
    user.save(update_fields=["tags"])
    connection.close()


def _set_tier(email, tier_slug):
    from accounts.models import User
    from payments.models import Tier

    user = User.objects.get(email=email)
    user.tier = Tier.objects.get(slug=tier_slug)
    user.save(update_fields=["tier"])
    connection.close()


@pytest.mark.django_db(transaction=True)
class TestTagPickerFiltersUserList:
    """Operator narrows the user list to one tag via the picker."""

    @pytest.mark.core
    def test_picker_filter_and_clear(self, django_server, browser):
        _ensure_tiers()
        staff_email = "tag-mgmt-admin@test.com"
        _create_staff_user(staff_email)
        _clear_users_except_staff(staff_email)
        _create_user("alice@test.com")
        _create_user("bob@test.com")
        _create_user("carol@test.com")
        _set_tags("alice@test.com", ["paid"])
        _set_tags("bob@test.com", ["paid"])
        _set_tags("carol@test.com", ["lapsed"])

        context = _auth_context(browser, staff_email)
        page = context.new_page()

        page.goto(
            f"{django_server}/studio/users/",
            wait_until="domcontentloaded",
        )
        body = page.content()
        assert "alice@test.com" in body
        assert "bob@test.com" in body
        assert "carol@test.com" in body

        # Pick "paid" from the dropdown -> page reloads.
        with page.expect_navigation(wait_until="domcontentloaded"):
            page.locator('[data-testid="user-tag-picker"]').select_option("paid")
        assert "tag=paid" in page.url
        body = page.content()
        assert "alice@test.com" in body
        assert "bob@test.com" in body
        assert "carol@test.com" not in body

        # Active tag pill is visible.
        active_chip = page.locator('[data-testid="active-tag-chip"]')
        assert active_chip.count() == 1
        assert "Tag: paid" in active_chip.inner_text()

        # Clear via the chip's x.
        page.locator('[data-testid="active-tag-clear"]').click()
        page.wait_for_load_state("domcontentloaded")
        assert "tag=" not in page.url
        body = page.content()
        assert "alice@test.com" in body
        assert "bob@test.com" in body
        assert "carol@test.com" in body

        context.close()


@pytest.mark.django_db(transaction=True)
class TestTagFilterSurvivesTierChip:
    """Tag filter is preserved when switching tier chips."""

    @pytest.mark.core
    def test_tag_filter_survives_tier_switch(self, django_server, browser):
        _ensure_tiers()
        staff_email = "tag-mgmt-tier@test.com"
        _create_staff_user(staff_email)
        _clear_users_except_staff(staff_email)
        _create_user("free-paid@test.com", tier_slug="free")
        _create_user("main-paid@test.com", tier_slug="main")
        _set_tags("free-paid@test.com", ["paid"])
        _set_tags("main-paid@test.com", ["paid"])

        context = _auth_context(browser, staff_email)
        page = context.new_page()

        page.goto(
            f"{django_server}/studio/users/?tag=paid",
            wait_until="domcontentloaded",
        )
        # Click "Main+" tier chip.
        page.locator('[data-filter="main_plus"]').first.click()
        page.wait_for_load_state("domcontentloaded")
        assert "tag=paid" in page.url
        body = page.content()
        assert "main-paid@test.com" in body
        assert "free-paid@test.com" not in body
        # Active tag pill is still visible.
        active_chip = page.locator('[data-testid="active-tag-chip"]')
        assert active_chip.count() == 1

        context.close()


@pytest.mark.django_db(transaction=True)
class TestTagRenamePropagates:
    """Operator renames a tag; the change propagates everywhere."""

    @pytest.mark.core
    def test_rename_tag_propagates(self, django_server, browser):
        _ensure_tiers()
        staff_email = "tag-mgmt-rename@test.com"
        _create_staff_user(staff_email)
        _clear_users_except_staff(staff_email)
        for email in ("alice@test.com", "bob@test.com", "carol@test.com"):
            _create_user(email)
            # Tags are stored already-normalized.
            _set_tags(email, ["paid-user"])

        from accounts.models import User

        alice_pk = User.objects.get(email="alice@test.com").pk
        connection.close()

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/users/{alice_pk}/",
            wait_until="domcontentloaded",
        )

        # Click the rename pencil for the 'paid-user' chip.
        page.locator(
            '[data-tag-rename-toggle="paid-user"]'
        ).click()
        rename_input = page.locator('[data-tag-rename-input="paid-user"]')
        rename_input.fill("paid")
        # Submit by clicking Apply.
        page.locator(
            '[data-tag-rename-form="paid-user"] [data-testid="user-tag-rename-apply"]'
        ).click()
        page.wait_for_load_state("domcontentloaded")

        success_message = page.locator(
            '[data-testid="messages-region"] [data-message-tag="success"]'
        )
        assert success_message.count() == 1
        assert (
            success_message.inner_text().strip()
            == 'Renamed "paid-user" to "paid" on 3 user(s).'
        )

        # The chip on this user now reads 'paid'.
        assert page.locator(
            '[data-testid="user-tag-chip"][data-tag="paid"]'
        ).count() == 1
        assert page.locator(
            '[data-testid="user-tag-chip"][data-tag="paid-user"]'
        ).count() == 0

        # /studio/users/?tag=paid-user returns zero users.
        page.goto(
            f"{django_server}/studio/users/?tag=paid-user",
            wait_until="domcontentloaded",
        )
        body = page.content()
        assert "alice@test.com" not in body
        assert "bob@test.com" not in body
        assert "carol@test.com" not in body

        # /studio/users/?tag=paid returns all three.
        page.goto(
            f"{django_server}/studio/users/?tag=paid",
            wait_until="domcontentloaded",
        )
        body = page.content()
        assert "alice@test.com" in body
        assert "bob@test.com" in body
        assert "carol@test.com" in body

        context.close()


@pytest.mark.django_db(transaction=True)
class TestTagRenameRejectsEmpty:
    """Renaming to an empty value surfaces a flash error."""

    @pytest.mark.core
    def test_rename_to_empty_is_rejected(self, django_server, browser):
        _ensure_tiers()
        staff_email = "tag-mgmt-empty@test.com"
        _create_staff_user(staff_email)
        _clear_users_except_staff(staff_email)
        _create_user("alice@test.com")
        _set_tags("alice@test.com", ["paid"])

        from accounts.models import User

        alice_pk = User.objects.get(email="alice@test.com").pk
        connection.close()

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/users/{alice_pk}/",
            wait_until="domcontentloaded",
        )

        page.locator('[data-tag-rename-toggle="paid"]').click()
        rename_input = page.locator('[data-tag-rename-input="paid"]')
        rename_input.fill("")
        page.locator(
            '[data-tag-rename-form="paid"] [data-testid="user-tag-rename-apply"]'
        ).click()
        page.wait_for_load_state("domcontentloaded")
        assert "New tag name cannot be empty." in page.content()
        # Original chip survives.
        assert page.locator(
            '[data-testid="user-tag-chip"][data-tag="paid"]'
        ).count() == 1

        context.close()


@pytest.mark.django_db(transaction=True)
class TestTagRenameDedupes:
    """Renaming collapses duplicates instead of creating them."""

    @pytest.mark.core
    def test_rename_collapses_duplicate(self, django_server, browser):
        _ensure_tiers()
        staff_email = "tag-mgmt-dedupe@test.com"
        _create_staff_user(staff_email)
        _clear_users_except_staff(staff_email)
        _create_user("alice@test.com")
        # Alice carries BOTH 'paid' and 'paid-user'.
        _set_tags("alice@test.com", ["paid", "paid-user"])

        from accounts.models import User

        alice_pk = User.objects.get(email="alice@test.com").pk
        connection.close()

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/users/{alice_pk}/",
            wait_until="domcontentloaded",
        )

        page.locator('[data-tag-rename-toggle="paid-user"]').click()
        page.locator('[data-tag-rename-input="paid-user"]').fill("paid")
        page.locator(
            '[data-tag-rename-form="paid-user"] [data-testid="user-tag-rename-apply"]'
        ).click()
        page.wait_for_load_state("domcontentloaded")

        # Exactly one 'paid' chip remains -- no duplicate.
        assert page.locator(
            '[data-testid="user-tag-chip"][data-tag="paid"]'
        ).count() == 1
        assert page.locator(
            '[data-testid="user-tag-chip"][data-tag="paid-user"]'
        ).count() == 0

        context.close()


@pytest.mark.django_db(transaction=True)
class TestTagDeleteEverywhere:
    """Operator deletes a tag and confirms the impact across users."""

    @pytest.mark.core
    def test_delete_flow_with_cancel_and_confirm(self, django_server, browser):
        _ensure_tiers()
        staff_email = "tag-mgmt-delete@test.com"
        _create_staff_user(staff_email)
        _clear_users_except_staff(staff_email)
        for i in range(5):
            email = f"user{i}@test.com"
            _create_user(email)
            _set_tags(email, ["early-adopter"])

        from accounts.models import User

        alice_pk = User.objects.get(email="user0@test.com").pk
        connection.close()

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/users/{alice_pk}/",
            wait_until="domcontentloaded",
        )

        # Open the confirm modal.
        page.locator('[data-tag-delete-open="early-adopter"]').click()
        dialog = page.locator(
            '[data-tag-delete-dialog="early-adopter"]'
        )
        copy = dialog.locator('[data-testid="user-tag-delete-copy"]')
        assert copy.count() == 1
        assert (
            "Delete tag early-adopter? This removes it from 5 users. "
            "Cannot be undone." in copy.inner_text()
        )

        # Cancel: the chip survives, no flash.
        page.locator(
            '[data-tag-delete-cancel="early-adopter"]'
        ).click()
        assert page.locator(
            '[data-testid="user-tag-chip"][data-tag="early-adopter"]'
        ).count() == 1

        # Open again and confirm.
        page.locator('[data-tag-delete-open="early-adopter"]').click()
        page.locator(
            '[data-tag-delete-dialog="early-adopter"] [data-testid="user-tag-delete-confirm"]'
        ).click()
        page.wait_for_load_state("domcontentloaded")
        body = page.content()
        assert 'Deleted tag' in body
        assert 'early-adopter' in body
        assert 'from 5 user(s)' in body
        # The chip is gone from this user.
        assert page.locator(
            '[data-testid="user-tag-chip"][data-tag="early-adopter"]'
        ).count() == 0

        # /studio/users/?tag=early-adopter returns zero users.
        page.goto(
            f"{django_server}/studio/users/?tag=early-adopter",
            wait_until="domcontentloaded",
        )
        body = page.content()
        for i in range(5):
            assert f"user{i}@test.com" not in body

        # The tag picker no longer offers it.
        page.goto(
            f"{django_server}/studio/users/",
            wait_until="domcontentloaded",
        )
        picker = page.locator('[data-testid="user-tag-picker"]')
        options = picker.locator('option').all_inner_texts()
        assert 'early-adopter' not in options

        context.close()


@pytest.mark.django_db(transaction=True)
class TestNonStaffCannotManageTags:
    """Non-staff cannot reach the rename / delete affordances."""

    @pytest.mark.core
    def test_non_staff_blocked_from_detail(self, django_server, browser):
        _ensure_tiers()
        staff_email = "tag-mgmt-gate@test.com"
        _create_staff_user(staff_email)
        _clear_users_except_staff(staff_email)
        _create_user("member@test.com")
        _set_tags("member@test.com", ["paid"])
        free_email = "free@test.com"
        _create_user(free_email, tier_slug="free")

        from accounts.models import User

        member_pk = User.objects.get(email="member@test.com").pk
        connection.close()

        context = _auth_context(browser, free_email)
        page = context.new_page()
        response = page.goto(
            f"{django_server}/studio/users/{member_pk}/",
            wait_until="domcontentloaded",
        )
        # Either 403 or redirect to login -- both are acceptable.
        assert response.status in (302, 403) or "/accounts/login/" in page.url
        # In any case, the rename + delete-everywhere affordances are not
        # in the body.
        body = page.content()
        assert 'data-testid="user-tag-rename-toggle"' not in body
        assert 'data-testid="user-tag-delete-everywhere"' not in body

        context.close()


@pytest.mark.django_db(transaction=True)
class TestPerUserRemoveIsSeparate:
    """Per-user remove (the small x) is distinct from delete-everywhere."""

    @pytest.mark.core
    def test_per_user_remove_only_affects_one_user(self, django_server, browser):
        _ensure_tiers()
        staff_email = "tag-mgmt-per-user@test.com"
        _create_staff_user(staff_email)
        _clear_users_except_staff(staff_email)
        emails = (
            "alice@test.com",
            "bob@test.com",
            "carol@test.com",
            "dan@test.com",
        )
        for email in emails:
            _create_user(email)
            _set_tags(email, ["paid"])

        from accounts.models import User

        alice_pk = User.objects.get(email="alice@test.com").pk
        connection.close()

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/users/{alice_pk}/",
            wait_until="domcontentloaded",
        )

        # Click the small 'x' on the 'paid' chip (per-user remove).
        page.locator(
            '[data-testid="user-tag-chip"][data-tag="paid"] [data-testid="user-tag-remove"]'
        ).click()
        page.wait_for_load_state("domcontentloaded")
        assert page.locator(
            '[data-testid="user-tag-chip"][data-tag="paid"]'
        ).count() == 0

        # The other three users still carry 'paid'.
        page.goto(
            f"{django_server}/studio/users/?tag=paid",
            wait_until="domcontentloaded",
        )
        body = page.content()
        assert "bob@test.com" in body
        assert "carol@test.com" in body
        assert "dan@test.com" in body
        assert "alice@test.com" not in body

        context.close()
