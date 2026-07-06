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
        # Issue #1127: the guide link points at the on-site docs page and
        # opens in a new tab.
        guide_link = page.locator('[data-testid="member-api-usage-guide-link"]')
        expect(guide_link).to_have_attribute("href", "/member-api/docs")
        expect(guide_link).to_have_attribute("target", "_blank")
        skill_link = page.locator('[data-testid="member-api-skill-link"]')
        expect(skill_link).to_have_attribute(
            "href",
            "https://github.com/AI-Shipping-Labs/website/tree/main/skills/ai-shipping-labs-plans-api",
        )
        expect(skill_link).to_have_attribute("target", "_blank")

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

    @pytest.mark.core
    def test_section_sits_below_email_preferences_with_calm_empty_state(
        self, django_server, browser
    ):
        # Issue #1127: the API keys section moved below Email Preferences
        # and the empty state is plain text (no dashed box).
        from django.db import connection

        create_user("member-api-key-order@test.com", tier_slug="free")
        connection.close()

        context = auth_context(browser, "member-api-key-order@test.com")
        page = context.new_page()
        page.goto(f"{django_server}/account/", wait_until="domcontentloaded")

        email_box = page.locator("#email-preferences-section")
        api_box = page.locator("#api-keys")
        email_y = email_box.bounding_box()["y"]
        api_y = api_box.bounding_box()["y"]
        assert api_y > email_y

        empty = page.locator('[data-testid="member-api-keys-empty"]')
        expect(empty).to_be_visible()
        # The empty state is plain muted text, not a dashed-border box.
        assert "border-dashed" not in (empty.get_attribute("class") or "")

        connection.close()
        context.close()

    @pytest.mark.core
    def test_guide_link_reaches_on_site_docs_page(self, django_server, browser):
        # Issue #1127: /member-api/docs resolves for a logged-in member.
        from django.db import connection

        create_user("member-api-key-docs@test.com", tier_slug="free")
        connection.close()

        context = auth_context(browser, "member-api-key-docs@test.com")
        page = context.new_page()

        response = page.goto(
            f"{django_server}/member-api/docs",
            wait_until="domcontentloaded",
        )
        assert response is not None
        assert response.status == 200

        connection.close()
        context.close()

    def test_skill_directory_exists_in_repo(self):
        # Issue #1127: the skill directory was restored so the tree link
        # no longer 404s.
        import os

        base = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "skills",
            "ai-shipping-labs-plans-api",
        )
        assert os.path.isfile(os.path.join(base, "README.md"))
        assert os.path.isfile(os.path.join(base, "SKILL.md"))

    @pytest.mark.core
    def test_member_revokes_then_deletes_key(self, django_server, browser):
        from django.db import connection

        from accounts.models import MemberAPIKey, User

        create_user("member-api-key-delete@test.com", tier_slug="free")
        user = User.objects.get(email="member-api-key-delete@test.com")
        MemberAPIKey.create_for_user(user=user, name="declutter me")
        connection.close()

        context = auth_context(browser, "member-api-key-delete@test.com")
        page = context.new_page()
        page.goto(f"{django_server}/account/#api-keys", wait_until="domcontentloaded")

        # Active key: revoke button present, no delete button yet.
        expect(page.locator('[data-testid="member-api-key-revoke"]')).to_be_visible()
        expect(page.locator('[data-testid="member-api-key-delete"]')).to_have_count(0)

        page.locator('[data-testid="member-api-key-revoke"]').click()
        page.wait_for_load_state("domcontentloaded")

        # Revoked: status badge and delete button now appear.
        expect(page.locator('[data-testid="member-api-key-revoked"]')).to_be_visible()
        expect(page.locator('[data-testid="member-api-key-delete"]')).to_be_visible()

        page.on("dialog", lambda dialog: dialog.accept())
        page.locator('[data-testid="member-api-key-delete"]').click()
        page.wait_for_load_state("domcontentloaded")

        assert "/account/" in page.url
        expect(page.locator('[data-testid="account-message"]')).to_contain_text(
            "deleted"
        )
        # The only key is gone -> empty state, no table.
        expect(page.locator('[data-testid="member-api-keys-empty"]')).to_be_visible()
        expect(page.locator('[data-testid="member-api-key-table"]')).to_have_count(0)

        assert MemberAPIKey.objects.filter(user=user).count() == 0
        connection.close()
        context.close()

    @pytest.mark.core
    def test_cancelling_delete_confirmation_keeps_the_key(
        self, django_server, browser
    ):
        from django.db import connection

        from accounts.models import MemberAPIKey, User

        create_user("member-api-key-cancel@test.com", tier_slug="free")
        user = User.objects.get(email="member-api-key-cancel@test.com")
        key, _ = MemberAPIKey.create_for_user(user=user, name="keep me")
        key.revoke()
        connection.close()

        context = auth_context(browser, "member-api-key-cancel@test.com")
        page = context.new_page()
        page.goto(f"{django_server}/account/#api-keys", wait_until="domcontentloaded")

        page.on("dialog", lambda dialog: dialog.dismiss())
        page.locator('[data-testid="member-api-key-delete"]').click()
        # No navigation should occur; the revoked row is still present.
        expect(page.locator('[data-testid="member-api-key-revoked"]')).to_be_visible()

        assert MemberAPIKey.objects.filter(pk=key.id).exists()
        connection.close()
        context.close()
