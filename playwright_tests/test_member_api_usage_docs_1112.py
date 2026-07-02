"""Playwright coverage for member Plans API usage docs links (#1112)."""

import os
import re

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import auth_context, create_user

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = pytest.mark.local_only


GITHUB_USAGE_GUIDE_URL = (
    "https://github.com/AI-Shipping-Labs/website/blob/main/"
    "docs/member-api/plans.md"
)
GITHUB_SKILL_URL = (
    "https://github.com/AI-Shipping-Labs/website/tree/main/"
    "skills/ai-shipping-labs-plans-api"
)


def _stub_github(page):
    page.route(
        "https://github.com/**",
        lambda route: route.fulfill(
            status=200,
            content_type="text/html",
            body="<html><body>GitHub stub</body></html>",
        ),
    )


@pytest.mark.django_db(transaction=True)
class TestMemberApiUsageDocsLinks:
    @pytest.mark.core
    def test_member_finds_api_help_from_account_settings(
        self,
        django_server,
        browser,
    ):
        email = "member-api-usage-links@test.com"
        create_user(email, tier_slug="free")
        context = auth_context(browser, email)
        page = context.new_page()
        _stub_github(page)

        page.goto(f"{django_server}/account/#api-keys", wait_until="domcontentloaded")
        expect(page.locator('[data-testid="member-api-keys-section"]')).to_be_visible()

        page.locator('[data-testid="member-api-usage-guide-link"]').click()
        page.wait_for_url(re.compile(r".*/docs/member-api/plans\.md$"))
        assert page.url == GITHUB_USAGE_GUIDE_URL

        page.goto(f"{django_server}/account/#api-keys", wait_until="domcontentloaded")
        page.locator('[data-testid="member-api-skill-link"]').click()
        page.wait_for_url(re.compile(r".*/skills/ai-shipping-labs-plans-api$"))
        assert page.url == GITHUB_SKILL_URL

        context.close()

    @pytest.mark.core
    def test_member_api_docs_point_to_usage_guide(self, django_server, browser):
        email = "member-api-docs-usage-guide@test.com"
        create_user(email, tier_slug="free")
        context = auth_context(browser, email)
        page = context.new_page()
        _stub_github(page)

        page.goto(f"{django_server}/member-api/docs", wait_until="domcontentloaded")
        expect(page.locator('[data-testid="member-api-docs"]')).to_be_visible()

        page.locator('[data-testid="member-api-usage-guide-link"]').click()
        page.wait_for_url(re.compile(r".*/docs/member-api/plans\.md$"))
        assert page.url == GITHUB_USAGE_GUIDE_URL

        context.close()
