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
    """Superuser mints a token in two clicks without picking a user.

    Covers two spec scenarios:
      - "Superuser mints a token in two clicks without picking a user"
      - "New token appears in the listing owned by the creating admin"
    """

    def test_superuser_creates_token_and_views_it_once(
        self, django_server, browser
    ):
        admin_email = "admin-token@test.com"
        _create_staff_user(admin_email)
        _delete_all_tokens()

        context = _auth_context(browser, admin_email)
        page = context.new_page()

        # 1. Empty state on the list page.
        page.goto(
            f"{django_server}/studio/api-tokens/",
            wait_until="domcontentloaded",
        )
        expect(
            page.locator('[data-testid="api-tokens-empty"]')
        ).to_be_visible()

        # 2. Click "Create token" button.
        page.locator('[data-testid="api-token-create-link"]').click()
        page.wait_for_load_state("domcontentloaded")
        assert "/studio/api-tokens/new/" in page.url

        # The form shows a Name input and confirms the token will be issued
        # to the signed-in admin. There is no user dropdown.
        expect(page.locator('[data-testid="token-name-input"]')).to_be_visible()
        owner_note = page.locator(
            '[data-testid="token-owner-note"]'
        ).inner_text()
        assert admin_email in owner_note
        expect(
            page.locator('[data-testid="token-user-select"]')
        ).to_have_count(0)

        # 3. Type the name and click Create.
        page.locator('[data-testid="token-name-input"]').fill("import script")
        page.locator('[data-testid="api-token-create-submit"]').click()
        page.wait_for_load_state("domcontentloaded")

        # 4. Landed on /created/. Plaintext key visible exactly once and the
        #    owner email shown is the signed-in admin.
        assert "/studio/api-tokens/created/" in page.url
        token_value = page.locator('[data-testid="api-token-value"]').inner_text()
        assert len(token_value) > 30
        warning = page.locator('[data-testid="api-token-warning"]').inner_text()
        assert "only time this token will be shown" in warning
        created_body = page.content()
        assert admin_email in created_body

        # 5. Back to list -- masked prefix only, full key absent from
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
        # The visible row carries the name and the creating admin's email.
        row_text = page.locator(
            '[data-testid="api-token-row"]'
        ).first.inner_text()
        assert "import script" in row_text
        assert admin_email in row_text
        # And the full key is NOT rendered as visible text in the cell.
        assert token_value not in row_text

        # 6. Navigating back to /created/ redirects to the list (one-shot).
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
class TestListingSurfacesOwnership:
    """Listing shows each token's owner so a superuser can tell them apart.

    Two superusers seeded with one token each (created directly via the ORM
    to simulate prior history). Signing in as one of them shows both rows
    with their respective owner emails in the User column.
    """

    def test_listing_shows_owner_for_each_row(self, django_server, browser):
        from accounts.models import Token, User

        admin_a = "admin-a@test.com"
        admin_b = "admin-b@test.com"
        _create_staff_user(admin_a)
        _create_staff_user(admin_b)
        _delete_all_tokens()
        user_a = User.objects.get(email=admin_a)
        user_b = User.objects.get(email=admin_b)
        Token.objects.create(user=user_a, name="token-a")
        Token.objects.create(user=user_b, name="token-b")
        connection.close()

        context = _auth_context(browser, admin_a)
        page = context.new_page()

        page.goto(
            f"{django_server}/studio/api-tokens/",
            wait_until="domcontentloaded",
        )

        rows = page.locator('[data-testid="api-token-row"]')
        expect(rows).to_have_count(2)

        # Collect every row's text and confirm each token name is paired
        # with the expected owner email in the same row.
        row_texts = rows.all_inner_texts()
        token_a_row = next(
            (t for t in row_texts if "token-a" in t), None
        )
        token_b_row = next(
            (t for t in row_texts if "token-b" in t), None
        )
        assert token_a_row is not None, f"token-a row missing: {row_texts}"
        assert token_b_row is not None, f"token-b row missing: {row_texts}"
        assert admin_a in token_a_row, (
            f"admin-a should own token-a, row text: {token_a_row}"
        )
        assert admin_b in token_b_row, (
            f"admin-b should own token-b, row text: {token_b_row}"
        )

        context.close()


@pytest.mark.django_db(transaction=True)
class TestReservedNameStillRejected:
    """Reserved system names are still rejected after the field is removed."""

    def test_reserved_system_name_blocks_creation(
        self, django_server, browser
    ):
        from accounts.models import Token

        admin_email = "admin-reserved@test.com"
        _create_staff_user(admin_email)
        _delete_all_tokens()

        context = _auth_context(browser, admin_email)
        page = context.new_page()

        page.goto(
            f"{django_server}/studio/api-tokens/new/",
            wait_until="domcontentloaded",
        )
        page.locator(
            '[data-testid="token-name-input"]'
        ).fill("studio-plan-editor")
        page.locator('[data-testid="api-token-create-submit"]').click()
        page.wait_for_load_state("domcontentloaded")

        # No redirect to /created/.
        assert "/studio/api-tokens/created/" not in page.url
        # The form re-renders with the reserved-name error.
        error_text = page.locator(
            '[data-testid="form-error-name"]'
        ).inner_text()
        assert "reserved" in error_text.lower()
        # No token was created.
        assert Token.objects.filter(name="studio-plan-editor").count() == 0
        connection.close()

        context.close()
