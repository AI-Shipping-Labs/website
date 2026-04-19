"""Playwright E2E tests for the mobile (hamburger) navigation menu (Issue #272).

Covers:
- Resources accordion is collapsed by default (chevron pointing down).
- Tapping Resources expands the list and rotates the chevron 180 degrees.
- Even when Resources is expanded, items below it (FAQ, Sign in / Account /
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

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")


MOBILE_VIEWPORTS = [
    {"width": 412, "height": 915},  # Pixel 7
    {"width": 375, "height": 667},  # iPhone SE
]


def _open_mobile_menu(page):
    """Tap the hamburger button and wait for the menu to be visible."""
    btn = page.locator("#mobile-menu-btn")
    btn.click()
    page.wait_for_selector("#mobile-menu:not(.hidden)", timeout=2000)


@pytest.mark.parametrize(
    "viewport",
    MOBILE_VIEWPORTS,
    ids=["pixel7-412", "iphonese-375"],
)
class TestMobileMenuResourcesAccordion:
    def test_resources_collapsed_by_default(
        self, django_server, browser, viewport
    ):
        """When the menu first opens, the Resources sub-list is hidden and
        the chevron is in its base (non-rotated) state."""
        context = browser.new_context(viewport=viewport)
        page = context.new_page()
        page.goto(f"{django_server}/", wait_until="domcontentloaded")

        _open_mobile_menu(page)

        resources_list = page.locator("#mobile-resources-list")
        # Element is in DOM but visually hidden via the `hidden` class.
        assert resources_list.count() == 1
        assert "hidden" in (resources_list.get_attribute("class") or ""), (
            "Resources list must start collapsed (hidden class present)"
        )
        assert not resources_list.is_visible(), (
            "Resources list must not be visible until tapped"
        )

        # Sub-links must not be reachable to a tap.
        blog_link = page.locator(
            '#mobile-resources-list a[href="/blog"]'
        )
        assert not blog_link.is_visible()

        chevron = page.locator("#mobile-resources-chevron")
        assert "rotate-180" not in (chevron.get_attribute("class") or ""), (
            "Chevron must not be rotated while Resources is collapsed"
        )

        context.close()

    def test_tapping_resources_expands_list_and_rotates_chevron(
        self, django_server, browser, viewport
    ):
        """Tapping the Resources toggle reveals the sub-list and rotates the
        chevron 180 degrees."""
        context = browser.new_context(viewport=viewport)
        page = context.new_page()
        page.goto(f"{django_server}/", wait_until="domcontentloaded")

        _open_mobile_menu(page)
        page.locator("#mobile-resources-toggle").click()

        resources_list = page.locator("#mobile-resources-list")
        assert "hidden" not in (resources_list.get_attribute("class") or "")
        assert resources_list.is_visible()

        # A representative sub-link is now visible / clickable.
        blog_link = page.locator(
            '#mobile-resources-list a[href="/blog"]'
        )
        assert blog_link.is_visible()

        chevron = page.locator("#mobile-resources-chevron")
        assert "rotate-180" in (chevron.get_attribute("class") or ""), (
            "Chevron must be rotated 180 when Resources is expanded"
        )

        context.close()

    def test_items_below_resources_remain_reachable_when_expanded(
        self, django_server, browser, viewport
    ):
        """When Resources is expanded the menu may exceed the viewport.
        The mobile-menu container itself must scroll (max-h + overflow-y-auto)
        so FAQ (anonymous user) is still reachable by scrolling within the
        menu, not by scrolling the page."""
        context = browser.new_context(viewport=viewport)
        page = context.new_page()
        page.goto(f"{django_server}/", wait_until="domcontentloaded")

        _open_mobile_menu(page)
        page.locator("#mobile-resources-toggle").click()

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

        # FAQ link must be present in the DOM and reachable by scrolling
        # within the menu container.
        faq = page.locator('#mobile-menu a[href="/faq"]')
        assert faq.count() == 1, "FAQ link must be inside the mobile menu"

        # Scroll the menu (not the page) into view of FAQ and assert it
        # becomes visible.
        faq.scroll_into_view_if_needed()
        assert faq.is_visible(), (
            "FAQ must be reachable via scrolling the mobile menu container"
        )

        context.close()


class TestMobileMenuAuthenticatedItemsReachable:
    """For an authenticated staff user the menu has more items below
    Resources (Notifications, Studio, Account, Log out). They must all be
    reachable when Resources is expanded."""

    def test_studio_and_logout_reachable_when_resources_expanded(
        self, django_server, browser
    ):
        _create_staff_user(email="mobilemenu-staff@test.com")
        context = _auth_context(browser, "mobilemenu-staff@test.com")
        # Override the desktop viewport from auth_context with a phone size.
        page = context.new_page()
        page.set_viewport_size({"width": 375, "height": 667})
        page.goto(f"{django_server}/", wait_until="domcontentloaded")

        _open_mobile_menu(page)
        page.locator("#mobile-resources-toggle").click()

        # Studio and Log out are below Resources for staff users.
        studio_link = page.locator('#mobile-menu a:has-text("Studio")')
        logout_link = page.locator('#mobile-menu a:has-text("Log out")')
        assert studio_link.count() >= 1
        assert logout_link.count() >= 1

        # Scrolling within the menu container brings them into view.
        logout_link.first.scroll_into_view_if_needed()
        assert logout_link.first.is_visible(), (
            "Log out must be reachable when Resources is expanded"
        )

        # The page itself should not need to scroll vertically; the menu
        # container's overflow-y handles it.
        scroll_y_after = page.evaluate("window.scrollY")
        assert scroll_y_after == 0, (
            "Page must not scroll; the mobile menu should scroll internally"
        )

        context.close()
