"""Playwright coverage for member API keys on /account/ (issue #1111)."""

import os

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import auth_context, create_user

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = pytest.mark.local_only


@pytest.mark.django_db(transaction=True)
class TestMemberAPIKeysAccountUI:
    @pytest.mark.core
    def test_member_creates_one_time_key_then_sees_only_masked_metadata(
        self, django_server, browser
    ):
        from django.db import connection

        create_user("member-api-key-ui@test.com", tier_slug="free")
        context = auth_context(browser, "member-api-key-ui@test.com")
        page = context.new_page()

        page.goto(f"{django_server}/account/#api-keys", wait_until="domcontentloaded")
        expect(page.locator('[data-testid="member-api-keys-section"]')).to_be_visible()
        expect(page.locator('[data-testid="member-api-keys-empty"]')).to_be_visible()

        page.locator('[data-testid="member-api-key-name-input"]').fill("local codex")
        page.locator('[data-testid="member-api-key-create-submit"]').click()
        page.wait_for_load_state("domcontentloaded")

        plaintext = page.locator(
            '[data-testid="member-api-key-plaintext"]'
        ).inner_text()
        assert plaintext.startswith("asl_member_")
        expect(page.locator('[data-testid="member-api-key-copy"]')).to_be_visible()

        page.goto(f"{django_server}/account/#api-keys", wait_until="domcontentloaded")
        body = page.content()
        assert plaintext not in body
        prefix = page.locator(
            '[data-testid="member-api-key-prefix"]'
        ).first.inner_text()
        assert prefix == f"{plaintext[:24]}..."
        assert "local codex" in page.locator(
            '[data-testid="member-api-key-row"]'
        ).first.inner_text()

        connection.close()
        context.close()

    @pytest.mark.core
    def test_member_revokes_old_key(self, django_server, browser):
        from django.db import connection

        from accounts.models import MemberAPIKey, User

        create_user("member-api-key-revoke@test.com", tier_slug="free")
        user = User.objects.get(email="member-api-key-revoke@test.com")
        member_key, plaintext = MemberAPIKey.create_for_user(
            user=user,
            name="old laptop",
        )
        connection.close()

        context = auth_context(browser, "member-api-key-revoke@test.com")
        page = context.new_page()
        page.goto(f"{django_server}/account/#api-keys", wait_until="domcontentloaded")

        page.locator('[data-testid="member-api-key-revoke"]').click()
        page.wait_for_load_state("domcontentloaded")

        expect(page.locator('[data-testid="member-api-key-revoked"]')).to_be_visible()
        assert MemberAPIKey.authenticate(plaintext) is None
        member_key.refresh_from_db()
        assert member_key.revoked_at is not None
        connection.close()
        context.close()

    @pytest.mark.core
    def test_anonymous_direct_management_url_redirects_to_login(
        self, django_server, browser
    ):
        page = browser.new_page()

        response = page.goto(
            f"{django_server}/account/api/member-api-keys",
            wait_until="domcontentloaded",
        )

        assert response is not None
        assert response.status == 200
        assert "/accounts/login/" in page.url
        assert 'data-testid="member-api-keys-section"' not in page.content()
        page.close()
