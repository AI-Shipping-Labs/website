"""Playwright E2E tests for the mobile (hamburger) navigation menu (Issue #272).

Covers:
- Learn and Community accordions are collapsed by default.
- Tapping each section expands the list and rotates the chevron 180 degrees.
- Even when text navigation is expanded, items below it (Sign in / Account /
  Studio / Logout) remain reachable because the menu container itself
  scrolls (max-h + overflow-y-auto), not the page.
- Behavior holds at both Pixel 7 (412px) and iPhone SE (375px) widths.
"""

import os

import pytest

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_staff_user as _create_staff_user,
)
from playwright_tests.conftest import (
    create_user as _create_user,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = pytest.mark.django_db(transaction=True)

MOBILE_VIEWPORTS = [
    {"width": 412, "height": 915},  # Pixel 7
    {"width": 375, "height": 667},  # iPhone SE
]


def _open_mobile_menu(page):
    """Tap the hamburger button and wait for the menu to be visible."""
    btn = page.locator("#mobile-menu-btn")
    btn.click()
    page.wait_for_selector("#mobile-menu:not(.hidden)", timeout=2000)


class TestMobileMenuHitTarget:
    def test_normal_click_opens_and_closes_at_390px(self, django_server, browser):
        context = browser.new_context(viewport={"width": 390, "height": 844})
        page = context.new_page()
        page.goto(f"{django_server}/", wait_until="domcontentloaded")

        btn = page.locator("#mobile-menu-btn")
        box = btn.bounding_box()
        assert box is not None
        assert box["width"] >= 44
        assert box["height"] >= 44

        hit_target_is_button = page.evaluate(
            """
            () => {
                const btn = document.getElementById('mobile-menu-btn');
                const rect = btn.getBoundingClientRect();
                const hit = document.elementFromPoint(
                    rect.left + rect.width / 2,
                    rect.top + rect.height / 2
                );
                return Boolean(hit && hit.closest('#mobile-menu-btn') === btn);
            }
            """
        )
        assert hit_target_is_button, (
            "Hamburger center point must not be intercepted by nearby header elements"
        )

        btn.click()
        page.wait_for_selector("#mobile-menu:not(.hidden)", timeout=2000)
        assert btn.get_attribute("aria-label") == "Close menu"

        public_links = [
            "Learn",
            "Community",
            "Sign in",
        ]
        for label in public_links:
            assert page.locator("#mobile-menu").get_by_text(label, exact=True).is_visible()

        btn.click()
        page.wait_for_function(
            "() => document.getElementById('mobile-menu').classList.contains('hidden')",
            timeout=2000,
        )
        assert btn.get_attribute("aria-label") == "Open menu"

        context.close()

    def test_desktop_header_keeps_hamburger_hidden(self, django_server, browser):
        context = browser.new_context(viewport={"width": 1024, "height": 768})
        page = context.new_page()
        page.goto(f"{django_server}/", wait_until="domcontentloaded")

        assert not page.locator("#mobile-menu-btn").is_visible()
        assert page.locator("#learn-dropdown-btn").is_visible()
        assert page.locator("#community-dropdown-btn").is_visible()
        assert page.locator("#resources-dropdown-btn").count() == 0

        context.close()


@pytest.mark.parametrize(
    "viewport",
    MOBILE_VIEWPORTS,
    ids=["pixel7-412", "iphonese-375"],
)
class TestMobileMenuTextNavAccordion:
    def test_text_nav_sections_collapsed_by_default(
        self, django_server, browser, viewport
    ):
        """When the menu first opens, text-nav sub-lists are hidden."""
        context = browser.new_context(viewport=viewport)
        page = context.new_page()
        page.goto(f"{django_server}/", wait_until="domcontentloaded")

        _open_mobile_menu(page)

        for section in ["learn", "community"]:
            section_list = page.locator(f"#mobile-{section}-list")
            assert section_list.count() == 1
            assert "hidden" in (section_list.get_attribute("class") or "")
            assert not section_list.is_visible()

            chevron = page.locator(f"#mobile-{section}-chevron")
            assert "rotate-180" not in (chevron.get_attribute("class") or "")

        context.close()

    def test_tapping_text_nav_sections_expands_lists_and_rotates_chevrons(
        self, django_server, browser, viewport
    ):
        context = browser.new_context(viewport=viewport)
        page = context.new_page()
        page.goto(f"{django_server}/", wait_until="domcontentloaded")

        _open_mobile_menu(page)

        expected = {
            "learn": [
                "Courses",
                "Workshops",
                "Learning Path",
                "Project Ideas",
                "Interview Prep",
                "Blog",
            ],
            "community": [
                "Community Sprints",
                "Events",
                "Activities",
                "Curated Links",
            ],
        }
        for section, labels in expected.items():
            page.locator(f"#mobile-{section}-toggle").click()
            section_list = page.locator(f"#mobile-{section}-list")
            assert "hidden" not in (section_list.get_attribute("class") or "")
            assert section_list.is_visible()
            for label in labels:
                assert section_list.get_by_text(label, exact=True).is_visible()
            chevron = page.locator(f"#mobile-{section}-chevron")
            assert "rotate-180" in (chevron.get_attribute("class") or "")

        context.close()

    def test_items_below_text_nav_remain_reachable_when_expanded(
        self, django_server, browser, viewport
    ):
        """When text nav is expanded the menu may exceed the viewport.
        The mobile-menu container itself must scroll (max-h + overflow-y-auto)
        so Sign in (anonymous user) is still reachable by scrolling within the
        menu, not by scrolling the page."""
        context = browser.new_context(viewport=viewport)
        page = context.new_page()
        page.goto(f"{django_server}/", wait_until="domcontentloaded")

        _open_mobile_menu(page)
        page.locator("#mobile-learn-toggle").click()
        page.locator("#mobile-community-toggle").click()

        # Container must declare a bounded height + scroll behavior.
        overflow_y, max_height = page.evaluate(
            """
            () => {
                const el = document.getElementById('mobile-menu');
                const cs = getComputedStyle(el);
                return [cs.overflowY, cs.maxHeight];
            }
            """
        )
        assert overflow_y in ("auto", "scroll"), (
            f"mobile-menu must be scrollable, got overflow-y={overflow_y!r}"
        )
        assert max_height not in ("none", ""), (
            f"mobile-menu must have a max-height, got {max_height!r}"
        )

        sign_in = page.locator('#mobile-menu a[href="/accounts/login/"]')
        assert sign_in.count() == 1, "Sign in link must be inside the mobile menu"

        # Scroll the menu (not the page) into view of Sign in and assert it
        # becomes visible.
        sign_in.scroll_into_view_if_needed()
        assert sign_in.is_visible(), (
            "Sign in must be reachable via scrolling the mobile menu container"
        )

        context.close()


class TestMobileMenuAuthenticatedItemsReachable:
    """For an authenticated staff user the menu has more items below
    the text nav (Notifications, Studio, Account, Log out). They must all be
    reachable when the text nav is expanded."""

    def test_member_account_actions_reachable(self, django_server, browser):
        _create_user(email="mobilemenu-member@test.com")
        context = _auth_context(browser, "mobilemenu-member@test.com")
        page = context.new_page()
        page.set_viewport_size({"width": 390, "height": 844})
        page.goto(f"{django_server}/", wait_until="domcontentloaded")

        _open_mobile_menu(page)

        for label in ["Notifications", "Account", "Log out"]:
            action = page.locator("#mobile-menu").get_by_text(label, exact=True)
            assert action.count() >= 1
            action.first.scroll_into_view_if_needed()
            assert action.first.is_visible()

        context.close()

    def test_studio_and_logout_reachable_when_text_nav_expanded(
        self, django_server, browser
    ):
        _create_staff_user(email="mobilemenu-staff@test.com")
        context = _auth_context(browser, "mobilemenu-staff@test.com")
        # Override the desktop viewport from auth_context with a phone size.
        page = context.new_page()
        page.set_viewport_size({"width": 375, "height": 667})
        page.goto(f"{django_server}/", wait_until="domcontentloaded")

        _open_mobile_menu(page)
        page.locator("#mobile-learn-toggle").click()
        page.locator("#mobile-community-toggle").click()

        # Studio and Log out are below the text nav for staff users.
        studio_link = page.locator('#mobile-menu a:has-text("Studio")')
        logout_link = page.locator('#mobile-menu a:has-text("Log out")')
        assert studio_link.count() >= 1
        assert logout_link.count() >= 1

        # Scrolling within the menu container brings them into view.
        logout_link.first.scroll_into_view_if_needed()
        assert logout_link.first.is_visible(), (
            "Log out must be reachable when text nav is expanded"
        )

        # The page itself should not need to scroll vertically; the menu
        # container's overflow-y handles it.
        scroll_y_after = page.evaluate("window.scrollY")
        assert scroll_y_after == 0, (
            "Page must not scroll; the mobile menu should scroll internally"
        )

        context.close()
