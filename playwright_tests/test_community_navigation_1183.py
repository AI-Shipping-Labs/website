"""Playwright coverage for issue #1183 community navigation IA."""

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
    ["nav-community-link-membership", "/pricing", "Membership"],
    ["nav-community-link-activities", "/activities#access-by-tier", "Activities"],
    ["nav-community-link-sprints", "/sprints", "Community Sprints"],
    ["nav-community-link-events", "/events", "Events"],
    [
        "nav-community-link-past-recordings",
        "/events?filter=past",
        "Past Recordings",
    ],
]

COMMUNITY_MOBILE_LINKS = [
    ["mobile-nav-community-link-membership", "/pricing", "Membership"],
    [
        "mobile-nav-community-link-activities",
        "/activities#access-by-tier",
        "Activities",
    ],
    ["mobile-nav-community-link-sprints", "/sprints", "Community Sprints"],
    ["mobile-nav-community-link-events", "/events", "Events"],
    [
        "mobile-nav-community-link-past-recordings",
        "/events?filter=past",
        "Past Recordings",
    ],
]


def _unique_email(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:8]}@example.com"


def _open_desktop_community_menu(page):
    page.get_by_test_id("nav-community-trigger").hover()
    menu = page.get_by_test_id("nav-community-menu")
    menu.wait_for(state="visible")
    return menu


def _open_mobile_community_menu(page):
    page.locator("#mobile-menu-btn").click()
    page.wait_for_selector("#mobile-menu:not(.hidden)", timeout=2000)
    page.get_by_test_id("mobile-nav-community-trigger").click()
    menu = page.get_by_test_id("mobile-nav-community-menu")
    menu.wait_for(state="visible")
    return menu


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


def test_desktop_community_dropdown_groups_membership_activities_sprints_events(
    django_server, page
):
    page.set_viewport_size({"width": 1280, "height": 800})
    page.goto(f"{django_server}/", wait_until="domcontentloaded")

    primary = page.get_by_test_id("desktop-primary-nav")
    trigger_ids = primary.evaluate(
        """
        el => [...el.querySelectorAll(':scope > .relative > button[data-testid]')]
            .map(button => button.getAttribute('data-testid'))
        """
    )
    assert trigger_ids == [
        "nav-about-trigger",
        "nav-community-trigger",
        "nav-resources-trigger",
    ]
    for absent_id in ["nav-membership", "nav-activities", "nav-sprints", "nav-events"]:
        assert primary.locator(f'[data-testid="{absent_id}"]').count() == 0

    menu = _open_desktop_community_menu(page)
    assert _menu_links(menu) == COMMUNITY_DESKTOP_LINKS


def test_visitor_moves_from_activities_to_pricing_sprints_and_events(
    django_server, page
):
    page.set_viewport_size({"width": 1280, "height": 800})
    page.goto(f"{django_server}/", wait_until="domcontentloaded")

    _open_desktop_community_menu(page)
    page.get_by_test_id("nav-community-link-activities").click()
    page.wait_for_url("**/activities#access-by-tier")
    expect(
        page.get_by_role("heading", name="Membership benefits by tier")
    ).to_be_visible()
    expect(page.get_by_test_id("activities-access-by-tier-section")).to_be_visible()

    page.get_by_test_id("activities-pricing-cta").click()
    page.wait_for_url("**/pricing")
    expect(
        page.get_by_role("heading", name="Choose your level of engagement")
    ).to_be_visible()

    _open_desktop_community_menu(page)
    page.get_by_test_id("nav-community-link-sprints").click()
    page.wait_for_url("**/sprints")
    expect(page.get_by_role("heading", name="Community Sprints")).to_be_visible()

    _open_desktop_community_menu(page)
    page.get_by_test_id("nav-community-link-events").click()
    page.wait_for_url("**/events")
    expect(page.get_by_role("heading", name="Live community events")).to_be_visible()


def test_mobile_community_accordion_mirrors_desktop_order_and_opens_activities(
    django_server, browser
):
    context = browser.new_context(viewport={"width": 390, "height": 844})
    page = context.new_page()
    try:
        page.goto(f"{django_server}/", wait_until="domcontentloaded")

        menu = _open_mobile_community_menu(page)
        assert _menu_links(menu) == COMMUNITY_MOBILE_LINKS

        page.get_by_test_id("mobile-nav-community-link-activities").click()
        page.wait_for_url("**/activities#access-by-tier")
        expect(
            page.get_by_role("heading", name="Membership benefits by tier")
        ).to_be_visible()
    finally:
        context.close()


def test_footer_community_column_links_to_major_destinations(django_server, page):
    page.goto(f"{django_server}/", wait_until="domcontentloaded")
    footer = page.locator("footer")
    expect(footer.get_by_role("heading", name="Community")).to_be_visible()

    expected = [
        ["About", "/about"],
        ["Membership Tiers", "/pricing"],
        ["Activities", "/activities#access-by-tier"],
        ["Community Sprints", "/sprints"],
        ["Events", "/events"],
        ["FAQ", "/faq"],
    ]
    for label, href in expected:
        link = footer.get_by_role("link", name=label, exact=True)
        expect(link).to_be_visible()
        assert link.get_attribute("href") == href

    community_links = footer.evaluate(
        """
        footer => {
            const heading = [...footer.querySelectorAll('h3')]
                .find(el => el.textContent.trim() === 'Community');
            return [...heading.nextElementSibling.querySelectorAll('a')]
                .map(a => [a.textContent.trim(), a.getAttribute('href')]);
        }
        """
    )
    assert community_links[:6] == expected

    footer.get_by_role("link", name="Activities", exact=True).click()
    page.wait_for_url("**/activities#access-by-tier")
    expect(
        page.get_by_role("heading", name="Membership benefits by tier")
    ).to_be_visible()


def test_authenticated_and_staff_controls_survive_community_navigation(
    django_server, browser, django_db_blocker
):
    member_email = _unique_email("nav1183-main")
    staff_email = _unique_email("nav1183-staff")
    with django_db_blocker.unblock():
        create_user(member_email, tier_slug="main", first_name="Member")
        create_staff_user(staff_email)

    member_context = auth_context(browser, member_email)
    member_page = member_context.new_page()
    try:
        member_page.set_viewport_size({"width": 1280, "height": 800})
        member_page.goto(f"{django_server}/", wait_until="domcontentloaded")
        assert _menu_links(_open_desktop_community_menu(member_page)) == (
            COMMUNITY_DESKTOP_LINKS
        )
        expect(member_page.locator("#notification-bell-btn")).to_be_visible()
        expect(member_page.get_by_test_id("account-menu-trigger")).to_be_visible()

        member_page.get_by_test_id("nav-community-link-activities").click()
        member_page.wait_for_url("**/activities#access-by-tier")
        expect(member_page.get_by_test_id("account-menu-trigger")).to_be_visible()
        member_page.get_by_test_id("account-menu-trigger").click()
        account_menu = member_page.get_by_test_id("account-menu-dropdown")
        expect(account_menu).to_be_visible()
        expect(account_menu.get_by_test_id("theme-toggle")).to_be_visible()
    finally:
        member_context.close()

    staff_context = auth_context(browser, staff_email)
    staff_page = staff_context.new_page()
    try:
        staff_page.set_viewport_size({"width": 1280, "height": 800})
        staff_page.goto(f"{django_server}/", wait_until="domcontentloaded")
        primary = staff_page.get_by_test_id("desktop-primary-nav")
        assert primary.get_by_text("Studio", exact=True).count() == 0
        _open_desktop_community_menu(staff_page)
        assert staff_page.get_by_test_id("nav-community-menu").get_by_text(
            "Studio", exact=True
        ).count() == 0
        staff_page.get_by_test_id("account-menu-trigger").click()
        expect(staff_page.get_by_test_id("account-menu-dropdown")).to_be_visible()
        expect(
            staff_page.get_by_test_id("account-menu-dropdown").get_by_role(
                "menuitem", name="Studio"
            )
        ).to_be_visible()
    finally:
        staff_context.close()


def test_resources_remain_content_focused_while_activities_stays_in_community(
    django_server, page
):
    page.set_viewport_size({"width": 1280, "height": 800})
    page.goto(f"{django_server}/", wait_until="domcontentloaded")

    page.get_by_test_id("nav-resources-trigger").hover()
    resources_menu = page.get_by_test_id("nav-resources-menu")
    resources_menu.wait_for(state="visible")
    expect(resources_menu.get_by_test_id("nav-resources-link-workshops")).to_have_attribute(
        "href", "/workshops"
    )
    assert resources_menu.get_by_text("Activities", exact=True).count() == 0

    community_menu = _open_desktop_community_menu(page)
    expect(community_menu.get_by_test_id("nav-community-link-activities")).to_have_attribute(
        "href", "/activities#access-by-tier"
    )
