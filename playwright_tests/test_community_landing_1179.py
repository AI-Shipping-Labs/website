import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import (
    auth_context,
    create_user,
    ensure_tiers,
    goto_with_retry,
)

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.core]


def test_old_community_bookmark_redirects_permanently_to_merged_home(
    django_server, page
):
    response = goto_with_retry(page, f"{django_server}/community")
    assert response.status == 200  # Playwright follows the permanent redirect.
    assert page.url == f"{django_server}/"
    expect(page.locator("#activities")).to_be_attached()
    expect(page.locator("#join-free")).to_be_attached()


def test_community_navigation_starts_with_membership_and_has_no_overview(
    django_server, page
):
    goto_with_retry(page, f"{django_server}/")
    page.get_by_test_id("nav-community-trigger").hover()
    menu = page.get_by_test_id("nav-community-menu")
    expect(menu).to_be_visible()
    links = menu.locator("a[data-testid]")
    expect(links.first).to_have_attribute("href", "/pricing")
    expect(links.first).to_have_text("Membership")
    expect(menu.locator('[data-testid$="overview"]')).to_have_count(0)
    expect(menu.locator('a[href="/community"]')).to_have_count(0)


@pytest.mark.local_only
def test_authenticated_community_bookmark_redirects_to_dashboard(
    django_server, browser, django_db_blocker
):
    with django_db_blocker.unblock():
        ensure_tiers()
        create_user("community-redirect-1241@example.com", tier_slug="main")
    context = auth_context(browser, "community-redirect-1241@example.com")
    page = context.new_page()
    try:
        goto_with_retry(page, f"{django_server}/community")
        assert page.url == f"{django_server}/"
        expect(page.locator("#join-free")).to_have_count(0)
        expect(page.get_by_role("heading", name="Recent content")).to_be_visible()
    finally:
        context.close()
