"""Playwright coverage for the current Community navigation contract (#1183)."""

import os
import uuid

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import auth_context, create_staff_user, create_user

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.local_only,
    pytest.mark.core,
]


COMMUNITY_DESKTOP_LINKS = [
    ["nav-community-link-events", "/events", "Events"],
    ["nav-community-link-sprints", "/sprints", "Community Sprints"],
    ["nav-community-link-books", "/books", "Book Club"],
]

COMMUNITY_MOBILE_LINKS = [
    ["mobile-nav-community-link-events", "/events", "Events"],
    ["mobile-nav-community-link-sprints", "/sprints", "Community Sprints"],
    ["mobile-nav-community-link-books", "/books", "Book Club"],
]


def _menu_links(menu):
    return menu.evaluate(
        """
        el => [...el.querySelectorAll('a[data-testid]')]
            .map(a => [
                a.getAttribute('data-testid'),
                a.getAttribute('href'),
                a.textContent.trim()
            ])
        """
    )


def _open_desktop_community(page):
    page.get_by_test_id("nav-community-trigger").hover()
    menu = page.get_by_test_id("nav-community-menu")
    menu.wait_for(state="visible")
    return menu


def _open_mobile_community(page):
    page.locator("#mobile-menu-btn").click()
    page.wait_for_selector("#mobile-menu:not(.hidden)")
    page.get_by_test_id("mobile-nav-community-trigger").click()
    menu = page.get_by_test_id("mobile-nav-community-menu")
    menu.wait_for(state="visible")
    return menu


def test_desktop_keeps_membership_top_level_and_removes_activities_from_community(
    django_server, page
):
    page.set_viewport_size({"width": 1280, "height": 800})
    page.goto(f"{django_server}/", wait_until="domcontentloaded")

    membership = page.get_by_test_id("nav-membership-link")
    expect(membership).to_have_attribute("href", "/membership")
    assert _menu_links(_open_desktop_community(page)) == COMMUNITY_DESKTOP_LINKS
    assert page.get_by_test_id("nav-community-link-activities").count() == 0

    membership.click()
    page.wait_for_url("**/membership")
    expect(
        page.get_by_role("heading", name="Choose your level of engagement")
    ).to_be_visible()


def test_mobile_mirrors_desktop_and_has_no_activities_item(django_server, browser):
    context = browser.new_context(viewport={"width": 390, "height": 844})
    page = context.new_page()
    page.goto(f"{django_server}/", wait_until="domcontentloaded")

    menu = _open_mobile_community(page)
    assert _menu_links(menu) == COMMUNITY_MOBILE_LINKS
    assert page.get_by_test_id("mobile-nav-community-link-activities").count() == 0
    membership = page.get_by_test_id("mobile-nav-membership-link")
    expect(membership).to_have_attribute("href", "/membership")
    context.close()


def test_community_links_reach_their_canonical_surfaces(django_server, page):
    page.set_viewport_size({"width": 1280, "height": 800})
    page.goto(f"{django_server}/", wait_until="domcontentloaded")

    destinations = (
        ("nav-community-link-events", "/events"),
        ("nav-community-link-sprints", "/sprints"),
        ("nav-community-link-books", "/books"),
    )
    for testid, destination in destinations:
        menu = _open_desktop_community(page)
        link = menu.get_by_test_id(testid)
        expect(link).to_have_attribute("href", destination)
        link.click()
        page.wait_for_url(f"**{destination}")
        page.goto(f"{django_server}/", wait_until="domcontentloaded")


def test_footer_activities_deep_link_targets_membership_benefits(
    django_server, page
):
    page.goto(f"{django_server}/", wait_until="domcontentloaded")

    footer = page.locator("footer")
    activities = footer.get_by_role("link", name="Activities", exact=True)
    expect(activities).to_have_attribute("href", "/membership#activities")
    activities.click()
    page.wait_for_url("**/membership#activities")
    expect(page.get_by_test_id("membership-benefits-section")).to_be_visible()


def test_authenticated_and_staff_controls_survive_navigation(
    django_server, browser, django_db_blocker
):
    member_email = f"nav1183-member-{uuid.uuid4().hex[:8]}@example.com"
    staff_email = f"nav1183-staff-{uuid.uuid4().hex[:8]}@example.com"
    with django_db_blocker.unblock():
        create_user(member_email, tier_slug="main")
        create_staff_user(staff_email)

    member_context = auth_context(browser, member_email)
    member_page = member_context.new_page()
    member_page.set_viewport_size({"width": 1280, "height": 800})
    member_page.goto(f"{django_server}/membership", wait_until="domcontentloaded")
    expect(member_page.get_by_test_id("account-menu-trigger")).to_be_visible()
    assert _menu_links(_open_desktop_community(member_page)) == COMMUNITY_DESKTOP_LINKS
    member_context.close()

    staff_context = auth_context(browser, staff_email)
    staff_page = staff_context.new_page()
    staff_page.set_viewport_size({"width": 1280, "height": 800})
    staff_page.goto(f"{django_server}/membership", wait_until="domcontentloaded")
    staff_page.get_by_test_id("account-menu-trigger").click()
    expect(
        staff_page.get_by_test_id("account-menu-dropdown").get_by_role(
            "menuitem", name="Studio"
        )
    ).to_be_visible()
    staff_context.close()
