"""Playwright coverage for the top nav restructure (issue #580).

Covers the top nav structure: the desktop primary nav groups About,
Community, and Resources; About contains About / Team / FAQ, Resources is
ordered with Blog first, Community starts with Membership,
Sprints, and Events, and the mobile menu mirrors the same structure with
three accordions.

File name preserved so historical test history stays grouped with the
earlier #545 grooming work.
"""

import datetime
import os
import re
import uuid
from pathlib import Path

import pytest

from playwright_tests.conftest import auth_context, create_staff_user, create_user

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.local_only]

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
    start_date=None,
):
    from django.db import connection

    from plans.models import Sprint

    # Default the start two weeks back so the date-derived badge (#979)
    # reads Active regardless of the calendar date the suite runs on
    # (started, not within W of the derived end). Callers can override.
    if start_date is None:
        start_date = datetime.date.today() - datetime.timedelta(days=14)

    sprint = Sprint.objects.create(
        name=name,
        slug=slug,
        start_date=start_date,
        duration_weeks=duration_weeks,
        status=status,
        min_tier_level=min_tier_level,
    )
    connection.close()
    return sprint


def _create_download(
    title="Public Download",
    slug="public-download",
):
    from django.db import connection

    from content.models import Download

    download = Download.objects.create(
        title=title,
        slug=slug,
        file_url=f"https://example.com/{slug}.pdf",
        published=True,
    )
    connection.close()
    return download


def _assert_no_horizontal_overflow(page):
    assert page.evaluate(
        "() => document.documentElement.scrollWidth <= "
        "document.documentElement.clientWidth + 1"
    )


def _desktop_top_level_test_ids(page):
    """Return the current top-level data-testids in document order."""
    return page.evaluate(
        """
        () => {
            const nav = document.querySelector('[data-testid="desktop-primary-nav"]');
            const ids = [
                'nav-community-trigger',
                'nav-learning-trigger',
                'nav-blog-link',
                'nav-membership-link',
                'nav-about-trigger',
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


def test_anonymous_top_nav_has_current_items_in_order(django_server, page):
    """Scenario: Anonymous visitor scans the top nav and finds the grouped IA."""
    page.set_viewport_size({"width": 1280, "height": 800})
    page.goto(f"{django_server}/", wait_until="domcontentloaded")

    assert _desktop_top_level_test_ids(page) == [
        "nav-community-trigger",
        "nav-learning-trigger",
        "nav-blog-link",
        "nav-membership-link",
        "nav-about-trigger",
    ]

    nav = page.locator('[data-testid="desktop-primary-nav"]')
    for duplicate_id in ["nav-membership", "nav-sprints", "nav-events"]:
        assert nav.locator(f'[data-testid="{duplicate_id}"]').count() == 0
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
    # "About" was dropped from the menu: it pointed at /about, the same
    # destination as "Team", so the duplicate entry was removed (commit
    # 39430dba). Team is the specific one that survives.
    assert link_ids == [
        "nav-about-link-team",
        "nav-about-link-faq",
    ]
    assert (
        page.get_by_test_id("nav-about-link-team").get_attribute("href")
        == "/about"
    )

    page.get_by_test_id("nav-about-link-team").click()
    page.wait_for_url("**/about")
    # Team anchor must exist on the about page.
    assert page.locator("#team").count() == 1
    _shot(page, "02-about-team-anchor")


def test_learning_dropdown_lists_current_learning_destinations(django_server, page):
    """Scenario: Visitor opens Learning and sees its canonical destinations."""
    page.set_viewport_size({"width": 1280, "height": 800})
    page.goto(f"{django_server}/", wait_until="domcontentloaded")

    page.get_by_test_id("nav-learning-trigger").hover()
    menu = page.get_by_test_id("nav-learning-menu")
    menu.wait_for(state="visible")

    link_ids = menu.evaluate(
        """
        el => [...el.querySelectorAll('a[data-testid]')]
            .map(a => a.getAttribute('data-testid'))
        """
    )
    assert link_ids == [
        "nav-learning-link-courses",
        "nav-learning-link-workshops",
        "nav-learning-link-learning-paths",
        "nav-learning-link-interview",
    ]

    # Verify the Learning Paths label is plural even though the route is singular.
    learning_link = page.get_by_test_id("nav-learning-link-learning-paths")
    assert learning_link.inner_text().strip() == "Learning Paths"
    assert learning_link.get_attribute("href") == "/learning-path/ai-engineer"
    assert menu.get_by_text("Past Recordings", exact=True).count() == 0
    assert menu.get_by_text("Event Recordings", exact=True).count() == 0
    assert menu.locator('a[href="/events?filter=past"]').count() == 0

    page.get_by_test_id("nav-learning-link-courses").click()
    page.wait_for_url("**/courses")
    _shot(page, "03-learning-destinations")


def test_learning_dropdown_adds_downloads_only_when_published(
    django_server, page, django_db_blocker
):
    with django_db_blocker.unblock():
        _create_download()

    page.set_viewport_size({"width": 1280, "height": 800})
    page.goto(f"{django_server}/", wait_until="domcontentloaded")

    page.get_by_test_id("nav-learning-trigger").hover()
    menu = page.get_by_test_id("nav-learning-menu")
    menu.wait_for(state="visible")

    downloads_link = page.get_by_test_id("nav-learning-link-downloads")
    assert downloads_link.count() == 1
    assert downloads_link.get_attribute("href") == "/downloads"


def test_community_dropdown_groups_current_surfaces(django_server, page):
    """Scenario: Visitor opens Community and finds current surfaces."""
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
        "nav-community-link-events",
        "nav-community-link-sprints",
        "nav-community-link-books",
    ]

    page.get_by_test_id("nav-community-link-events").click()
    page.wait_for_url("**/events")
    _shot(page, "04-community-events")


def test_community_dropdown_links_events_first(django_server, page):
    """Scenario: Visitor opens Community and starts at Events."""
    page.set_viewport_size({"width": 1280, "height": 800})
    page.goto(f"{django_server}/", wait_until="domcontentloaded")

    page.get_by_test_id("nav-community-trigger").hover()
    menu = page.get_by_test_id("nav-community-menu")
    menu.wait_for(state="visible")

    links = menu.evaluate(
        """
        el => [...el.querySelectorAll('a[data-testid]')]
            .map(a => [a.getAttribute('data-testid'), a.getAttribute('href')])
        """
    )
    assert links[:3] == [
        ["nav-community-link-events", "/events"],
        ["nav-community-link-sprints", "/sprints"],
        ["nav-community-link-books", "/books"],
    ]


def test_community_links_reach_membership_sprints_and_events(django_server, page):
    """Scenario: Visitor reaches community surfaces from the Community menu."""
    page.set_viewport_size({"width": 1280, "height": 800})
    page.goto(f"{django_server}/", wait_until="domcontentloaded")

    page.get_by_test_id("nav-community-trigger").hover()
    page.get_by_test_id("nav-community-menu").wait_for(state="visible")
    sprints = page.get_by_test_id("nav-community-link-sprints")
    assert sprints.get_attribute("href") == "/sprints"
    sprints.click()
    page.wait_for_url("**/sprints")

    page.go_back()
    page.wait_for_load_state("domcontentloaded")

    page.get_by_test_id("nav-community-trigger").hover()
    page.get_by_test_id("nav-community-menu").wait_for(state="visible")
    events = page.get_by_test_id("nav-community-link-events")
    assert events.get_attribute("href") == "/events"
    events.click()
    page.wait_for_url("**/events")
    _shot(page, "05-sprints-events-community")


def test_desktop_events_page_exposes_past_filter(
    django_server, page
):
    """Scenario: Visitor finds past recordings from desktop community navigation."""
    page.set_viewport_size({"width": 1280, "height": 800})
    page.goto(f"{django_server}/", wait_until="domcontentloaded")

    page.get_by_test_id("nav-community-trigger").hover()
    menu = page.get_by_test_id("nav-community-menu")
    menu.wait_for(state="visible")

    page.get_by_test_id("nav-community-link-events").click()
    page.get_by_test_id("events-filter-past").click()
    page.wait_for_url(re.compile(r".*/events\?filter=past$"))
    assert page.get_by_test_id("events-filter-past").get_attribute(
        "aria-selected"
    ) == "true"
    _shot(page, "05b-desktop-past-recordings")


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
    # "About" dropped from the menu (commit 39430dba): it duplicated the
    # /about destination of "Team".
    assert link_ids == [
        "mobile-nav-about-link-team",
        "mobile-nav-about-link-faq",
    ]

    page.get_by_test_id("mobile-nav-about-link-faq").click()
    page.wait_for_url("**/faq")
    # Mobile menu collapses when navigating away (close-on-link-click + reload).
    _shot(page, "06-mobile-about-faq")
    context.close()


def test_mobile_learning_accordion_lists_current_destinations(django_server, browser):
    """Scenario: Mobile visitor expands Learning."""
    context = browser.new_context(viewport={"width": 390, "height": 844})
    page = context.new_page()
    page.goto(f"{django_server}/", wait_until="domcontentloaded")

    _open_mobile_menu(page)
    page.get_by_test_id("mobile-nav-learning-trigger").click()
    resources_menu = page.get_by_test_id("mobile-nav-learning-menu")
    resources_menu.wait_for(state="visible")

    link_ids = resources_menu.evaluate(
        """
        el => [...el.querySelectorAll('a[data-testid]')]
            .map(a => a.getAttribute('data-testid'))
        """
    )
    assert link_ids == [
        "mobile-nav-learning-link-courses",
        "mobile-nav-learning-link-workshops",
        "mobile-nav-learning-link-learning-paths",
        "mobile-nav-learning-link-interview",
    ]
    learning_link = page.get_by_test_id("mobile-nav-learning-link-learning-paths")
    assert learning_link.inner_text().strip() == "Learning Paths"

    page.get_by_test_id("mobile-nav-learning-link-courses").click()
    page.wait_for_url("**/courses")
    _shot(page, "07-mobile-learning")
    context.close()


def test_mobile_community_accordion_reaches_events_past_filter(django_server, browser):
    """Scenario: Mobile visitor reaches Events and its past filter."""
    context = browser.new_context(viewport={"width": 390, "height": 844})
    page = context.new_page()
    page.goto(f"{django_server}/", wait_until="domcontentloaded")

    _open_mobile_menu(page)
    page.get_by_test_id("mobile-nav-community-trigger").click()
    community_menu = page.get_by_test_id("mobile-nav-community-menu")
    community_menu.wait_for(state="visible")

    link_ids = community_menu.evaluate(
        """
        el => [...el.querySelectorAll('a[data-testid]')]
            .map(a => a.getAttribute('data-testid'))
        """
    )
    assert link_ids == [
        "mobile-nav-community-link-events",
        "mobile-nav-community-link-sprints",
        "mobile-nav-community-link-books",
    ]

    page.get_by_test_id("mobile-nav-community-link-events").click()
    page.get_by_test_id("events-filter-past").click()
    page.wait_for_url(re.compile(r".*/events\?filter=past$"))
    assert page.get_by_test_id("events-filter-past").get_attribute(
        "aria-selected"
    ) == "true"
    assert page.get_by_test_id("events-past-section").is_visible()
    _shot(page, "07b-mobile-past-recordings")
    context.close()


def test_mobile_top_level_items_follow_current_order(django_server, browser):
    """Scenario: Mobile visitor sees the current public IA order."""
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
                'mobile-nav-community-trigger',
                'mobile-nav-learning-trigger',
                'mobile-nav-blog-link',
                'mobile-nav-membership-link',
                'mobile-nav-about-trigger',
            ]);
            return [...menu.querySelectorAll('[data-testid]')]
                .map(el => el.getAttribute('data-testid'))
                .filter(id => wanted.has(id));
        }
        """
    )
    assert test_ids == [
        "mobile-nav-community-trigger",
        "mobile-nav-learning-trigger",
        "mobile-nav-blog-link",
        "mobile-nav-membership-link",
        "mobile-nav-about-trigger",
    ]
    for duplicate_id in [
        "mobile-nav-membership",
        "mobile-nav-sprints",
        "mobile-nav-events",
    ]:
        assert page.locator(f'#mobile-menu [data-testid="{duplicate_id}"]').count() == 0
    _shot(page, "08-mobile-cluster-order")
    context.close()


def test_mobile_320px_has_no_horizontal_overflow(django_server, browser):
    """Scenario: Mobile visitor at 320px width has no horizontal overflow."""
    context = browser.new_context(viewport={"width": 320, "height": 568})
    page = context.new_page()
    page.goto(f"{django_server}/", wait_until="domcontentloaded")

    _open_mobile_menu(page)
    page.get_by_test_id("mobile-nav-community-trigger").click()
    page.get_by_test_id("mobile-nav-learning-trigger").click()
    page.get_by_test_id("mobile-nav-about-trigger").click()

    scroll_width = page.evaluate("() => document.documentElement.scrollWidth")
    assert scroll_width <= 320 + 1, (
        f"document horizontally overflows at 320px viewport: {scroll_width}"
    )

    # Every nav link inside the mobile menu must be reachable by scrolling
    # the menu container (no horizontal overflow forcing them off-canvas).
    for test_id in [
        "mobile-nav-about-link-team",
        "mobile-nav-community-link-events",
        "mobile-nav-community-link-sprints",
        "mobile-nav-learning-link-courses",
        "mobile-nav-learning-link-interview",
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
        "nav-community-trigger",
        "nav-learning-trigger",
        "nav-blog-link",
        "nav-membership-link",
        "nav-about-trigger",
    ]
    assert page.locator("#notification-bell-btn").is_visible()
    assert page.locator("#account-menu-trigger").is_visible()
    # Sign-in button is not present when authenticated.
    assert page.get_by_role("link", name="Sign in").count() == 0

    page.get_by_test_id("nav-community-trigger").hover()
    menu = page.get_by_test_id("nav-community-menu")
    menu.wait_for(state="visible")
    link_ids = menu.evaluate(
        "el => [...el.querySelectorAll('a[data-testid]')]"
        ".map(a => a.getAttribute('data-testid'))"
    )
    assert link_ids == [
        "nav-community-link-events",
        "nav-community-link-sprints",
        "nav-community-link-books",
    ]

    page.get_by_test_id("nav-community-link-events").click()
    page.get_by_test_id("events-filter-past").click()
    page.wait_for_url(re.compile(r".*/events\?filter=past$"))
    assert page.get_by_test_id("events-filter-past").get_attribute(
        "aria-selected"
    ) == "true"
    assert page.locator("#notification-bell-btn").is_visible()
    assert page.locator("#account-menu-trigger").is_visible()
    page.locator("#account-menu-trigger").click()
    dropdown = page.locator("#account-menu-dropdown")
    dropdown.wait_for(state="visible")
    assert dropdown.get_by_test_id("theme-toggle").is_visible()
    assert dropdown.get_by_role("menuitem", name="Log out").is_visible()
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


@pytest.mark.core
def test_sprints_page_lists_active_sprint(django_server, page, django_db_blocker):
    with django_db_blocker.unblock():
        _clear_sprints()
        _create_sprint()

    page.goto(f"{django_server}/sprints", wait_until="domcontentloaded")

    card = page.locator('[data-testid="sprints-sprint-card"]').first
    assert card.is_visible()
    text = card.inner_text()
    assert "May Shipping Sprint" in text
    # Badge is date-derived (#979): the sprint started two weeks ago and
    # runs four weeks, so it reads ACTIVE (CSS-uppercased) on any run date.
    assert "ACTIVE" in text
    assert "(4 weeks)" in text
    assert "Main or above" in text
    assert card.locator('[data-testid="sprints-sprint-link"]').get_attribute("href") == (
        "/sprints/may-shipping-sprint"
    )
    assert card.locator('[data-testid="sprints-sprint-cta"]').count() == 0


def test_sprints_page_empty_state(django_server, page, django_db_blocker):
    with django_db_blocker.unblock():
        _clear_sprints()

    page.goto(f"{django_server}/sprints", wait_until="domcontentloaded")

    empty = page.locator('[data-testid="sprints-empty"]')
    assert empty.is_visible()
    assert "Next sprint coming soon" in empty.inner_text()
    assert page.locator('[data-testid="sprints-sprint-card"]').count() == 0


def test_existing_activities_url_redirects_to_membership_benefits(django_server, page):
    page.goto(f"{django_server}/activities", wait_until="domcontentloaded")

    page.wait_for_url(f"{django_server}/membership#activities")
    assert page.locator('[data-testid="membership-benefits-section"]').is_visible()
    assert page.get_by_role(
        "heading", name="Build with structure, support, and a group"
    ).is_visible()
