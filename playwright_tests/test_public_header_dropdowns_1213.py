"""Keyboard accessibility coverage for public header dropdowns (issue #1213)."""

import os
import uuid

import pytest

from playwright_tests.conftest import auth_context, create_user

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.local_only,
    pytest.mark.core,
]


def _email(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:8]}@example.com"


def _active_test_id(page):
    return page.evaluate(
        "() => document.activeElement && document.activeElement.dataset.testid"
    )


def _tab_to_test_id(page, test_id, *, max_tabs=20):
    for _ in range(max_tabs):
        page.keyboard.press("Tab")
        if _active_test_id(page) == test_id:
            return
    raise AssertionError(f"Could not reach {test_id!r} by tabbing")


def _assert_open(page, section):
    trigger = page.locator(f'[data-testid="nav-{section}-trigger"]')
    menu = page.locator(f'[data-testid="nav-{section}-menu"]')
    menu.wait_for(state="visible", timeout=3000)
    assert trigger.get_attribute("aria-expanded") == "true"
    assert menu.is_visible()


def _assert_closed(page, section):
    trigger = page.locator(f'[data-testid="nav-{section}-trigger"]')
    menu = page.locator(f'[data-testid="nav-{section}-menu"]')
    assert trigger.get_attribute("aria-expanded") == "false"
    assert not menu.is_visible()


def _tab_through_links(page, section, link_ids):
    for link_id in link_ids:
        page.keyboard.press("Tab")
        assert _active_test_id(page) == link_id
        assert page.locator(f'[data-testid="nav-{section}-menu"]').is_visible()


def test_anonymous_keyboard_about_menu_reaches_faq(django_server, page):
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(f"{django_server}/", wait_until="domcontentloaded")

    _tab_to_test_id(page, "nav-about-trigger")
    _assert_open(page, "about")
    page.keyboard.press("Enter")
    _assert_open(page, "about")

    _tab_through_links(
        page,
        "about",
        [
            "nav-about-link-team",
            "nav-about-link-faq",
        ],
    )
    page.keyboard.press("Enter")
    page.wait_for_url(f"{django_server}/faq", timeout=5000)


def test_anonymous_keyboard_community_menu_reaches_membership(
    django_server, page
):
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(f"{django_server}/", wait_until="domcontentloaded")

    _tab_to_test_id(page, "nav-community-trigger")
    _assert_open(page, "community")
    page.keyboard.press(" ")
    _assert_open(page, "community")

    _tab_through_links(
        page,
        "community",
        [
            "nav-community-link-membership",
        ],
    )
    page.keyboard.press("Enter")
    page.wait_for_url(f"{django_server}/pricing", timeout=5000)


def test_anonymous_keyboard_resources_menu_reaches_workshops(
    django_server, page
):
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(f"{django_server}/", wait_until="domcontentloaded")

    _tab_to_test_id(page, "nav-resources-trigger")
    _assert_open(page, "resources")
    page.keyboard.press("Enter")
    _assert_open(page, "resources")

    _tab_through_links(
        page,
        "resources",
        [
            "nav-resources-link-blog",
            "nav-resources-link-courses",
            "nav-resources-link-workshops",
        ],
    )
    page.keyboard.press("Enter")
    page.wait_for_url(f"{django_server}/workshops", timeout=5000)


def test_resources_menu_stays_open_until_focus_leaves_or_escape(
    django_server, page
):
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(f"{django_server}/", wait_until="domcontentloaded")

    _tab_to_test_id(page, "nav-resources-trigger")
    _assert_open(page, "resources")
    _tab_through_links(
        page,
        "resources",
        [
            "nav-resources-link-blog",
            "nav-resources-link-courses",
            "nav-resources-link-workshops",
            "nav-resources-link-learning-paths",
            "nav-resources-link-projects",
            "nav-resources-link-interview",
            "nav-resources-link-curated-links",
        ],
    )

    page.keyboard.press("Tab")
    _assert_closed(page, "resources")

    page.goto(f"{django_server}/", wait_until="domcontentloaded")
    _tab_to_test_id(page, "nav-about-trigger")
    _assert_open(page, "about")
    page.keyboard.press("Escape")
    _assert_closed(page, "about")
    assert _active_test_id(page) == "nav-about-trigger"


def test_public_dropdowns_switch_and_close_on_outside_click(django_server, page):
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(f"{django_server}/", wait_until="domcontentloaded")

    _tab_to_test_id(page, "nav-about-trigger")
    _assert_open(page, "about")
    _tab_to_test_id(page, "nav-community-trigger", max_tabs=8)
    _assert_closed(page, "about")
    _assert_open(page, "community")

    page.mouse.click(20, 260)
    _assert_closed(page, "community")
    assert page.url.rstrip("/") == django_server.rstrip("/")


def test_signed_in_header_dropdowns_are_mutually_exclusive(
    django_server, browser, django_db_blocker
):
    email = _email("public-header-1213")
    with django_db_blocker.unblock():
        create_user(email=email, tier_slug="free", first_name="Ada")

    context = auth_context(browser, email)
    page = context.new_page()
    page.set_viewport_size({"width": 1280, "height": 900})
    try:
        page.goto(f"{django_server}/", wait_until="domcontentloaded")

        _tab_to_test_id(page, "nav-resources-trigger")
        _assert_open(page, "resources")

        account_trigger = page.locator("#account-menu-trigger")
        account_trigger.click()
        page.locator("#account-menu-dropdown").wait_for(
            state="visible", timeout=3000
        )
        _assert_closed(page, "resources")
        assert account_trigger.get_attribute("aria-expanded") == "true"

        notification_trigger = page.locator("#notification-bell-btn")
        notification_trigger.click()
        page.locator("#notification-dropdown").wait_for(
            state="visible", timeout=5000
        )
        assert account_trigger.get_attribute("aria-expanded") == "false"
        assert notification_trigger.get_attribute("aria-expanded") == "true"

        page.locator('[data-testid="nav-about-trigger"]').focus()
        _assert_open(page, "about")
        assert notification_trigger.get_attribute("aria-expanded") == "false"
        assert not page.locator("#notification-dropdown").is_visible()
    finally:
        context.close()


def test_pointer_hover_still_opens_about_menu_and_team_link(
    django_server, page
):
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(f"{django_server}/", wait_until="domcontentloaded")

    page.locator('[data-testid="nav-about-trigger"]').hover()
    _assert_open(page, "about")
    page.locator('[data-testid="nav-about-link-team"]').click()
    page.wait_for_url(f"{django_server}/about", timeout=5000)


def test_mobile_public_nav_accordions_keep_existing_aria_and_links(
    django_server, browser
):
    context = browser.new_context(viewport={"width": 390, "height": 844})
    page = context.new_page()
    try:
        page.goto(f"{django_server}/", wait_until="domcontentloaded")
        page.locator("#mobile-menu-btn").click()
        page.locator("#mobile-menu:not(.hidden)").wait_for(
            state="visible", timeout=3000
        )

        expected = {
            "about": ["Team", "FAQ"],
            "community": [
                "Membership",
                "Activities",
                "Community Sprints",
                "Events",
                "Past Recordings",
            ],
            "resources": [
                "Blog",
                "Courses",
                "Workshops",
                "Learning Paths",
                "Project Ideas",
                "Interview Prep",
                "Curated Links",
            ],
        }
        for section, labels in expected.items():
            trigger = page.locator(f'[data-testid="mobile-nav-{section}-trigger"]')
            menu = page.locator(f'[data-testid="mobile-nav-{section}-menu"]')
            assert trigger.get_attribute("aria-expanded") == "false"
            assert not menu.is_visible()

            trigger.click()
            assert trigger.get_attribute("aria-expanded") == "true"
            assert menu.is_visible()
            for label in labels:
                assert menu.get_by_text(label, exact=True).is_visible()

            trigger.click()
            assert trigger.get_attribute("aria-expanded") == "false"
            assert not menu.is_visible()
    finally:
        context.close()
