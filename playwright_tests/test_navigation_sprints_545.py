"""Playwright coverage for the top nav restructure (issue #580).

Covers all 11 scenarios from the spec: the desktop primary nav reads
About / Membership / Community / Sprints / Events / Resources, About is
a dropdown containing About / Team / FAQ, Resources is reordered with
Blog first, Community surfaces Membership + Sprints + Events, and the
mobile menu mirrors the same structure with three accordions.

File name preserved so historical test history stays grouped with the
earlier #545 grooming work.
"""

import datetime
import os
import uuid
from pathlib import Path

import pytest

from playwright_tests.conftest import auth_context, create_staff_user, create_user

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = pytest.mark.django_db(transaction=True)

SCREENSHOT_DIR = Path(__file__).resolve().parent.parent / ".tmp" / "aisl-issue-580-screenshots"


def _shot(page, name):
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SCREENSHOT_DIR / f"{name}.png", full_page=False)


def _email(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:8]}@example.com"


def _clear_sprints():
    from django.db import connection

    from plans.models import Plan, Sprint, SprintEnrollment

    Plan.objects.all().delete()
    SprintEnrollment.objects.all().delete()
    Sprint.objects.all().delete()
    connection.close()


def _create_sprint(
    name="May Shipping Sprint",
    slug="may-shipping-sprint",
    status="active",
    min_tier_level=20,
    duration_weeks=4,
):
    from django.db import connection

    from plans.models import Sprint

    sprint = Sprint.objects.create(
        name=name,
        slug=slug,
        start_date=datetime.date(2026, 5, 15),
        duration_weeks=duration_weeks,
        status=status,
        min_tier_level=min_tier_level,
    )
    connection.close()
    return sprint


def _assert_no_horizontal_overflow(page):
    assert page.evaluate(
        "() => document.documentElement.scrollWidth <= "
        "document.documentElement.clientWidth + 1"
    )


def _desktop_top_level_test_ids(page):
    """Return the top-level data-testids in left-to-right document order."""
    return page.evaluate(
        """
        () => {
            const nav = document.querySelector('[data-testid="desktop-primary-nav"]');
            const ids = [
                'nav-about-trigger',
                'nav-membership',
                'nav-community-trigger',
                'nav-sprints',
                'nav-events',
                'nav-resources-trigger',
            ];
            const found = [...nav.querySelectorAll('[data-testid]')]
                .map(el => el.getAttribute('data-testid'))
                .filter(id => ids.includes(id));
            // Dedupe preserving order.
            return [...new Set(found)];
        }
        """
    )


# ---------------------------------------------------------------------------
# Desktop scenarios
# ---------------------------------------------------------------------------


def test_anonymous_top_nav_has_six_destinations_in_order(django_server, page):
    """Scenario: Anonymous visitor scans the top nav and finds the six destinations."""
    page.set_viewport_size({"width": 1280, "height": 800})
    page.goto(f"{django_server}/", wait_until="domcontentloaded")

    assert _desktop_top_level_test_ids(page) == [
        "nav-about-trigger",
        "nav-membership",
        "nav-community-trigger",
        "nav-sprints",
        "nav-events",
        "nav-resources-trigger",
    ]

    nav = page.locator('[data-testid="desktop-primary-nav"]')
    # FAQ is not a top-level destination — only inside About.
    assert nav.get_by_role("link", name="FAQ").count() == 0
    # Activities is not a top-level destination (regression #555).
    assert nav.get_by_role("link", name="Activities").count() == 0

    _assert_no_horizontal_overflow(page)
    _shot(page, "01-anonymous-top-nav-order")


def test_about_dropdown_contains_about_team_faq(django_server, page):
    """Scenario: Visitor hovers About to find Team and FAQ."""
    page.set_viewport_size({"width": 1280, "height": 800})
    page.goto(f"{django_server}/", wait_until="domcontentloaded")

    page.get_by_test_id("nav-about-trigger").hover()
    menu = page.get_by_test_id("nav-about-menu")
    menu.wait_for(state="visible")

    link_ids = menu.evaluate(
        """
        el => [...el.querySelectorAll('a[data-testid]')]
            .map(a => a.getAttribute('data-testid'))
        """
    )
    assert link_ids == [
        "nav-about-link-about",
        "nav-about-link-team",
        "nav-about-link-faq",
    ]
    assert (
        page.get_by_test_id("nav-about-link-team").get_attribute("href")
        == "/about#team"
    )

    page.get_by_test_id("nav-about-link-team").click()
    page.wait_for_url("**/about#team")
    # Team anchor must exist on the about page.
    assert page.locator("#team").count() == 1
    _shot(page, "02-about-team-anchor")


def test_resources_dropdown_lists_blog_first(django_server, page):
    """Scenario: Visitor opens Resources and sees Blog first."""
    page.set_viewport_size({"width": 1280, "height": 800})
    page.goto(f"{django_server}/", wait_until="domcontentloaded")

    page.get_by_test_id("nav-resources-trigger").hover()
    menu = page.get_by_test_id("nav-resources-menu")
    menu.wait_for(state="visible")

    link_ids = menu.evaluate(
        """
        el => [...el.querySelectorAll('a[data-testid]')]
            .map(a => a.getAttribute('data-testid'))
        """
    )
    assert link_ids == [
        "nav-resources-link-blog",
        "nav-resources-link-courses",
        "nav-resources-link-workshops",
        "nav-resources-link-learning-paths",
        "nav-resources-link-projects",
        "nav-resources-link-interview",
        "nav-resources-link-curated-links",
    ]

    # Verify the Learning Paths label is plural even though the route is singular.
    learning_link = page.get_by_test_id("nav-resources-link-learning-paths")
    assert learning_link.inner_text().strip() == "Learning Paths"
    assert learning_link.get_attribute("href") == "/learning-path/ai-engineer"

    page.get_by_test_id("nav-resources-link-blog").click()
    page.wait_for_url("**/blog")
    _shot(page, "03-resources-blog-first")


def test_community_dropdown_groups_membership_sprints_events(django_server, page):
    """Scenario: Visitor opens Community and finds Membership grouped with surfaces."""
    page.set_viewport_size({"width": 1280, "height": 800})
    page.goto(f"{django_server}/", wait_until="domcontentloaded")

    page.get_by_test_id("nav-community-trigger").hover()
    menu = page.get_by_test_id("nav-community-menu")
    menu.wait_for(state="visible")

    link_ids = menu.evaluate(
        """
        el => [...el.querySelectorAll('a[data-testid]')]
            .map(a => a.getAttribute('data-testid'))
        """
    )
    assert link_ids == [
        "nav-community-link-membership",
        "nav-community-link-sprints",
        "nav-community-link-events",
    ]

    page.get_by_test_id("nav-community-link-membership").click()
    page.wait_for_url("**/pricing")
    _shot(page, "04-community-membership")


def test_top_level_sprints_and_events_link_directly(django_server, page):
    """Scenario: Visitor reaches Sprints and Events from one click at top level."""
    page.set_viewport_size({"width": 1280, "height": 800})
    page.goto(f"{django_server}/", wait_until="domcontentloaded")

    sprints = page.get_by_test_id("nav-sprints")
    assert sprints.get_attribute("href") == "/sprints"
    sprints.click()
    page.wait_for_url("**/sprints")

    page.go_back()
    page.wait_for_load_state("domcontentloaded")

    events = page.get_by_test_id("nav-events")
    assert events.get_attribute("href") == "/events"
    events.click()
    page.wait_for_url("**/events")
    _shot(page, "05-sprints-events-direct")


# ---------------------------------------------------------------------------
# Mobile scenarios
# ---------------------------------------------------------------------------


def _open_mobile_menu(page):
    page.locator("#mobile-menu-btn").click()
    page.wait_for_selector("#mobile-menu:not(.hidden)", timeout=2000)


def test_mobile_about_accordion_exposes_team_and_faq(django_server, browser):
    """Scenario: Mobile visitor expands the About accordion to find Team and FAQ."""
    context = browser.new_context(viewport={"width": 390, "height": 844})
    page = context.new_page()
    page.goto(f"{django_server}/", wait_until="domcontentloaded")

    _open_mobile_menu(page)

    page.get_by_test_id("mobile-nav-about-trigger").click()
    about_menu = page.get_by_test_id("mobile-nav-about-menu")
    about_menu.wait_for(state="visible")

    link_ids = about_menu.evaluate(
        """
        el => [...el.querySelectorAll('a[data-testid]')]
            .map(a => a.getAttribute('data-testid'))
        """
    )
    assert link_ids == [
        "mobile-nav-about-link-about",
        "mobile-nav-about-link-team",
        "mobile-nav-about-link-faq",
    ]

    page.get_by_test_id("mobile-nav-about-link-faq").click()
    page.wait_for_url("**/faq")
    # Mobile menu collapses when navigating away (close-on-link-click + reload).
    _shot(page, "06-mobile-about-faq")
    context.close()


def test_mobile_resources_accordion_lists_blog_first(django_server, browser):
    """Scenario: Mobile visitor expands Resources and sees Blog first."""
    context = browser.new_context(viewport={"width": 390, "height": 844})
    page = context.new_page()
    page.goto(f"{django_server}/", wait_until="domcontentloaded")

    _open_mobile_menu(page)
    page.get_by_test_id("mobile-nav-resources-trigger").click()
    resources_menu = page.get_by_test_id("mobile-nav-resources-menu")
    resources_menu.wait_for(state="visible")

    link_ids = resources_menu.evaluate(
        """
        el => [...el.querySelectorAll('a[data-testid]')]
            .map(a => a.getAttribute('data-testid'))
        """
    )
    assert link_ids == [
        "mobile-nav-resources-link-blog",
        "mobile-nav-resources-link-courses",
        "mobile-nav-resources-link-workshops",
        "mobile-nav-resources-link-learning-paths",
        "mobile-nav-resources-link-projects",
        "mobile-nav-resources-link-interview",
        "mobile-nav-resources-link-curated-links",
    ]
    learning_link = page.get_by_test_id("mobile-nav-resources-link-learning-paths")
    assert learning_link.inner_text().strip() == "Learning Paths"

    page.get_by_test_id("mobile-nav-resources-link-blog").click()
    page.wait_for_url("**/blog")
    _shot(page, "07-mobile-resources-blog")
    context.close()


def test_mobile_direct_links_appear_between_about_and_resources(django_server, browser):
    """Scenario: Mobile visitor reads the cluster of direct links between accordions."""
    context = browser.new_context(viewport={"width": 390, "height": 844})
    page = context.new_page()
    page.goto(f"{django_server}/", wait_until="domcontentloaded")

    _open_mobile_menu(page)

    # Read mobile menu test ids top-to-bottom and assert the cluster order.
    test_ids = page.evaluate(
        """
        () => {
            const menu = document.getElementById('mobile-menu');
            const wanted = new Set([
                'mobile-nav-about-trigger',
                'mobile-nav-membership',
                'mobile-nav-community-trigger',
                'mobile-nav-sprints',
                'mobile-nav-events',
                'mobile-nav-resources-trigger',
            ]);
            return [...menu.querySelectorAll('[data-testid]')]
                .map(el => el.getAttribute('data-testid'))
                .filter(id => wanted.has(id));
        }
        """
    )
    assert test_ids == [
        "mobile-nav-about-trigger",
        "mobile-nav-membership",
        "mobile-nav-community-trigger",
        "mobile-nav-sprints",
        "mobile-nav-events",
        "mobile-nav-resources-trigger",
    ]
    _shot(page, "08-mobile-cluster-order")
    context.close()


def test_mobile_320px_has_no_horizontal_overflow(django_server, browser):
    """Scenario: Mobile visitor at 320px width has no horizontal overflow."""
    context = browser.new_context(viewport={"width": 320, "height": 568})
    page = context.new_page()
    page.goto(f"{django_server}/", wait_until="domcontentloaded")

    _open_mobile_menu(page)
    page.get_by_test_id("mobile-nav-about-trigger").click()
    page.get_by_test_id("mobile-nav-community-trigger").click()
    page.get_by_test_id("mobile-nav-resources-trigger").click()

    scroll_width = page.evaluate("() => document.documentElement.scrollWidth")
    assert scroll_width <= 320 + 1, (
        f"document horizontally overflows at 320px viewport: {scroll_width}"
    )

    # Every nav link inside the mobile menu must be reachable by scrolling
    # the menu container (no horizontal overflow forcing them off-canvas).
    for test_id in [
        "mobile-nav-about-link-team",
        "mobile-nav-community-link-sprints",
        "mobile-nav-resources-link-curated-links",
    ]:
        link = page.get_by_test_id(test_id)
        link.scroll_into_view_if_needed()
        assert link.is_visible()

    _shot(page, "09-mobile-320-no-overflow")
    context.close()


# ---------------------------------------------------------------------------
# Authenticated + staff scenarios
# ---------------------------------------------------------------------------


def test_authenticated_member_sees_same_public_nav_plus_account(
    django_server, browser, django_db_blocker
):
    """Scenario: Authenticated member sees the same public nav plus account controls."""
    email = _email("member-580")
    with django_db_blocker.unblock():
        create_user(email, tier_slug="main")

    context = auth_context(browser, email)
    page = context.new_page()
    page.set_viewport_size({"width": 1280, "height": 800})
    page.goto(f"{django_server}/", wait_until="domcontentloaded")

    assert _desktop_top_level_test_ids(page) == [
        "nav-about-trigger",
        "nav-membership",
        "nav-community-trigger",
        "nav-sprints",
        "nav-events",
        "nav-resources-trigger",
    ]
    assert page.locator("#notification-bell-btn").is_visible()
    assert page.locator("#account-menu-trigger").is_visible()
    # Sign-in button is not present when authenticated.
    assert page.get_by_role("link", name="Sign in").count() == 0

    page.get_by_test_id("nav-about-trigger").hover()
    menu = page.get_by_test_id("nav-about-menu")
    menu.wait_for(state="visible")
    link_ids = menu.evaluate(
        "el => [...el.querySelectorAll('a[data-testid]')]"
        ".map(a => a.getAttribute('data-testid'))"
    )
    assert link_ids == [
        "nav-about-link-about",
        "nav-about-link-team",
        "nav-about-link-faq",
    ]
    _shot(page, "10-member-nav")
    context.close()


def test_staff_studio_link_lives_in_account_menu_not_public_nav(
    django_server, browser, django_db_blocker
):
    """Scenario: Staff member sees Studio in the account menu, not the public nav."""
    email = _email("staff-580")
    with django_db_blocker.unblock():
        create_staff_user(email)

    context = auth_context(browser, email)
    page = context.new_page()
    page.set_viewport_size({"width": 1280, "height": 800})
    page.goto(f"{django_server}/", wait_until="domcontentloaded")

    nav = page.locator('[data-testid="desktop-primary-nav"]')
    assert nav.get_by_role("link", name="Studio").count() == 0

    page.locator("#account-menu-trigger").click()
    dropdown = page.locator("#account-menu-dropdown")
    dropdown.wait_for(state="visible")
    assert dropdown.get_by_role("menuitem", name="Studio").is_visible()
    _shot(page, "11-staff-studio-account-menu")
    context.close()


# ---------------------------------------------------------------------------
# Sprints page regression tests (kept from earlier #545 work)
# ---------------------------------------------------------------------------


def test_sprints_page_lists_active_sprint(django_server, page, django_db_blocker):
    with django_db_blocker.unblock():
        _clear_sprints()
        _create_sprint()

    page.goto(f"{django_server}/sprints", wait_until="domcontentloaded")

    card = page.locator('[data-testid="sprints-sprint-card"]').first
    assert card.is_visible()
    text = card.inner_text()
    assert "May Shipping Sprint" in text
    assert "ACTIVE" in text
    assert "May 15, 2026" in text
    assert "4 weeks" in text
    assert "Membership: Main" in text
    assert card.locator('[data-testid="sprints-sprint-cta"]').get_attribute("href") == (
        "/accounts/login/?next=/sprints/may-shipping-sprint"
    )


def test_sprints_page_empty_state(django_server, page, django_db_blocker):
    with django_db_blocker.unblock():
        _clear_sprints()

    page.goto(f"{django_server}/sprints", wait_until="domcontentloaded")

    empty = page.locator('[data-testid="sprints-empty"]')
    assert empty.is_visible()
    assert "Next sprint coming soon" in empty.inner_text()
    assert page.locator('[data-testid="sprints-sprint-card"]').count() == 0


def test_existing_activities_page_still_loads(django_server, page):
    page.goto(f"{django_server}/activities", wait_until="domcontentloaded")

    assert page.locator('[data-testid="activities-sprints-section"]').is_visible()
    assert page.get_by_text("Member activities and support").is_visible()
