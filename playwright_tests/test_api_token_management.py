"""Playwright E2E for the Studio API token management UI (issue #431).

The token-creation flow has a "show plaintext once, copy to clipboard"
interaction that warrants a real browser test. The API contract itself is
covered by Django tests in ``api/tests/`` -- these tests cover the operator
UX flow only.

Usage:
    uv run pytest playwright_tests/test_api_token_management.py -v
"""

import os

import pytest
from playwright.sync_api import expect

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


def _create_staff_only_user(email):
    """Create an is_staff=True is_superuser=False user."""
    from accounts.models import User

    _ensure_tiers()
    user, _ = User.objects.get_or_create(
        email=email,
        defaults={
            "email_verified": True,
            "is_staff": True,
            "is_superuser": False,
        },
    )
    user.set_password("TestPass123!")
    user.is_staff = True
    user.is_superuser = False
    user.email_verified = True
    user.save()
    connection.close()
    return user


def _delete_all_tokens():
    from accounts.models import Token

    Token.objects.all().delete()
    connection.close()


@pytest.mark.django_db(transaction=True)
class TestSuperuserIssuesToken:
    """Superuser issues a token, sees it once, then only sees the prefix."""

    def test_superuser_creates_token_and_views_it_once(
        self, django_server, browser
    ):
        admin_email = "admin-token@test.com"
        staff_email = "staff-token@test.com"
        _create_staff_user(admin_email)
        _create_staff_only_user(staff_email)
        _delete_all_tokens()

        context = _auth_context(browser, admin_email)
        page = context.new_page()

        # 1. Sidebar link is visible.
        page.goto(
            f"{django_server}/studio/", wait_until="domcontentloaded"
        )
        expect(page.locator('[data-testid="api-tokens-nav-link"]')).to_be_visible()

        # 2. Empty state on the list page.
        page.locator('[data-testid="api-tokens-nav-link"]').click()
        page.wait_for_load_state("domcontentloaded")
        assert "/studio/api-tokens/" in page.url
        expect(
            page.locator('[data-testid="api-tokens-empty"]')
        ).to_be_visible()

        # 3. Click "Create token" button.
        page.locator('[data-testid="api-token-create-link"]').click()
        page.wait_for_load_state("domcontentloaded")
        assert "/studio/api-tokens/new/" in page.url

        # 4. Fill name + select staff user, submit.
        page.locator('[data-testid="token-name-input"]').fill("import script")
        page.locator('[data-testid="token-user-select"]').select_option(
            label=staff_email,
        )
        page.locator('[data-testid="api-token-create-submit"]').click()
        page.wait_for_load_state("domcontentloaded")

        # 5. Landed on /created/. Plaintext key visible exactly once.
        assert "/studio/api-tokens/created/" in page.url
        token_value = page.locator('[data-testid="api-token-value"]').inner_text()
        assert len(token_value) > 30
        warning = page.locator('[data-testid="api-token-warning"]').inner_text()
        assert "only time this token will be shown" in warning

        # 6. Back to list -- masked prefix only, full key absent from
        #    visible cells. (The revoke form's action URL legitimately
        #    contains the key as a path parameter; that's not a "display"
        #    of the key.)
        page.locator('[data-testid="api-token-back-to-list"]').click()
        page.wait_for_load_state("domcontentloaded")
        assert page.url.rstrip("/").endswith("/studio/api-tokens")
        # Prefix appears in the visible key column.
        prefix_cell_text = page.locator(
            '[data-testid="api-token-prefix"]'
        ).inner_text().strip()
        assert prefix_cell_text == f"{token_value[:8]}..."
        # The visible row carries the name and assigned user.
        row_text = page.locator(
            '[data-testid="api-token-row"]'
        ).first.inner_text()
        assert "import script" in row_text
        assert staff_email in row_text
        # And the full key is NOT rendered as visible text in the cell.
        assert token_value not in row_text

        # 7. Navigating back to /created/ redirects to the list (one-shot).
        page.goto(
            f"{django_server}/studio/api-tokens/created/",
            wait_until="domcontentloaded",
        )
        assert page.url.rstrip("/").endswith("/studio/api-tokens")

        context.close()


@pytest.mark.django_db(transaction=True)
class TestNonSuperuserStaffCannotReachTokens:
    """Staff (not superuser) cannot see the link or reach the page."""

    def test_staff_user_blocked(self, django_server, browser):
        staff_email = "staff-only-tokens@test.com"
        _create_staff_only_user(staff_email)

        context = _auth_context(browser, staff_email)
        page = context.new_page()

        # 1. Sidebar link is NOT visible.
        page.goto(
            f"{django_server}/studio/", wait_until="domcontentloaded"
        )
        expect(
            page.locator('[data-testid="api-tokens-nav-link"]')
        ).to_have_count(0)

        # 2. Direct navigation 403s.
        response = page.goto(
            f"{django_server}/studio/api-tokens/",
            wait_until="domcontentloaded",
        )
        assert response is not None
        assert response.status == 403

        context.close()


@pytest.mark.django_db(transaction=True)
class TestSuperuserRevokesToken:
    """Superuser revokes a token via the list page."""

    def test_revoke_removes_row_and_flashes_success(
        self, django_server, browser
    ):
        from accounts.models import Token, User

        admin_email = "admin-revoke@test.com"
        _create_staff_user(admin_email)
        _delete_all_tokens()
        admin = User.objects.get(email=admin_email)
        Token.objects.create(user=admin, name="old-laptop")
        connection.close()

        context = _auth_context(browser, admin_email)
        page = context.new_page()

        page.goto(
            f"{django_server}/studio/api-tokens/",
            wait_until="domcontentloaded",
        )
        body_before = page.content()
        assert "old-laptop" in body_before

        # Auto-confirm the JS confirm() dialog.
        page.on("dialog", lambda dialog: dialog.accept())
        page.locator('[data-testid="api-token-revoke"]').click()
        page.wait_for_load_state("domcontentloaded")

        # Still on the list page; the row is gone.
        assert page.url.rstrip("/").endswith("/studio/api-tokens")
        body_after = page.content()
        assert "old-laptop" not in body_after
        assert "Token revoked" in body_after

        context.close()


@pytest.mark.django_db(transaction=True)
class TestCreateFormRejectsNonAdmins:
    """Free / Basic / Main / Premium contacts must NOT appear in the dropdown."""

    def test_dropdown_excludes_regular_member(self, django_server, browser):
        admin_email = "admin-dropdown@test.com"
        staff_email = "staff-dropdown@test.com"
        member_email = "member-dropdown@test.com"
        _create_staff_user(admin_email)
        _create_staff_only_user(staff_email)
        _create_user(member_email, tier_slug="free")

        context = _auth_context(browser, admin_email)
        page = context.new_page()

        page.goto(
            f"{django_server}/studio/api-tokens/new/",
            wait_until="domcontentloaded",
        )
        # Read the option list off the dropdown.
        select = page.locator('[data-testid="token-user-select"]')
        options = select.locator("option").all_inner_texts()
        assert any(staff_email in o for o in options), (
            f"Staff user must appear in dropdown, got {options}"
        )
        assert not any(member_email in o for o in options), (
            f"Free member must not appear in dropdown, got {options}"
        )

        context.close()
