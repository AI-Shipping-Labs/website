"""End-to-end tests for the Studio sidebar layout fix (issue #351).

The desktop sidebar previously used ``position: fixed`` with no explicit
``top``, so on Studio pages that render an in-flow banner (env-mismatch,
impersonation) the sidebar's computed top offset matched the banner's
height. When the user scrolled past the banner the sidebar stayed pinned
to that offset, leaving a visible gap above its top edge.

The fix replaces ``fixed h-full`` with responsive ``md:sticky md:top-0
md:h-screen md:self-start`` so the sidebar sits in the flex flow on
desktop (sits below the banner at scroll=0; pins to viewport top once the
banner scrolls past) while keeping the mobile drawer pattern (`fixed
inset-y-0` toggled by JS).

These tests cover the five Playwright scenarios listed in the issue:
  1. Operator scrolls a long studio page; sidebar stays anchored to
     viewport (no gap), even when an env-mismatch banner is present.
  2. Sidebar pins to top of viewport when no banner is rendered (the
     impersonation banner is absent for non-impersonating sessions, and
     a configured matching SITE_BASE_URL hides the env-mismatch banner).
  3. Sidebar nav scrolls independently when its content overflows.
  4. Mobile drawer still opens and closes via the toggle button.
  5. No horizontal overflow at common viewport widths.
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
    ensure_tiers as _ensure_tiers,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")


# Default SITE_BASE_URL in settings is ``https://aishippinglabs.com`` — when
# the dev server runs on 127.0.0.1 the env-mismatch banner reliably renders.
# A small tolerance covers sub-pixel rounding when comparing y-coordinates.
PIXEL_TOLERANCE = 1.5


# ---------------------------------------------------------------------------
# Scenario 1: scrolling a Studio page with the env-mismatch banner present
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestSidebarStaysAnchoredWithBanner:
    """No gap appears between the viewport top and the sidebar after scroll."""

    def test_sidebar_pins_to_viewport_top_after_scrolling_past_banner(
        self, django_server, browser
    ):
        _ensure_tiers()
        _create_staff_user("admin@test.com")

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        page.set_viewport_size({"width": 1280, "height": 800})

        # /studio/settings reliably triggers the env-mismatch banner because
        # the test server's host (127.0.0.1) does not match SITE_BASE_URL.
        page.goto(
            f"{django_server}/studio/settings",
            wait_until="domcontentloaded",
        )

        # The banner is rendered (regression guard for the test setup).
        banner = page.locator('[data-testid="env-mismatch-banner"]')
        assert banner.count() == 1
        banner_box = banner.bounding_box()
        assert banner_box is not None

        # At scroll=0 the sidebar's top edge sits at (or just below) the
        # banner's bottom edge — they're both in the flex flow.
        sidebar = page.locator("aside#studio-sidebar")
        sidebar_box = sidebar.bounding_box()
        assert sidebar_box is not None
        assert sidebar_box["y"] >= banner_box["y"] + banner_box["height"] - PIXEL_TOLERANCE
        assert sidebar_box["y"] <= banner_box["y"] + banner_box["height"] + PIXEL_TOLERANCE

        # Scroll the page so the banner moves off screen.
        page.evaluate("window.scrollTo(0, 600)")
        page.wait_for_function(
            """() => {
                const sidebar = document.querySelector('aside#studio-sidebar');
                return sidebar && sidebar.getBoundingClientRect().y <= 1.5;
            }"""
        )

        # The sidebar's top edge is now flush with the viewport top — no gap.
        sidebar_box_after = sidebar.bounding_box()
        assert sidebar_box_after is not None
        assert sidebar_box_after["y"] <= PIXEL_TOLERANCE, (
            f"Sidebar top should be at viewport y=0 after scrolling past the banner, "
            f"got y={sidebar_box_after['y']}"
        )

        # And the sidebar fills the viewport height (h-screen on md+).
        viewport_height = page.evaluate("window.innerHeight")
        assert (
            abs(sidebar_box_after["height"] - viewport_height) <= PIXEL_TOLERANCE
        ), (
            f"Sidebar height should match viewport height ({viewport_height}), "
            f"got {sidebar_box_after['height']}"
        )


# ---------------------------------------------------------------------------
# Scenario 2: scrolling a Studio page with no preceding banner
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestSidebarPinsWhenNoBanner:
    """When no banner is rendered the sidebar starts at y=0 and stays there."""

    def test_sidebar_top_is_zero_with_matching_site_base_url(
        self, django_server, browser, settings
    ):
        # Configure SITE_BASE_URL to match the live request host so the
        # env-mismatch banner does not render. The setting is read by the
        # context processor on every request, so updating it here is enough.
        settings.SITE_BASE_URL = django_server

        _ensure_tiers()
        _create_staff_user("admin@test.com")

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        page.set_viewport_size({"width": 1280, "height": 800})

        page.goto(f"{django_server}/studio/", wait_until="domcontentloaded")

        # No banner is rendered.
        banner = page.locator('[data-testid="env-mismatch-banner"]')
        assert banner.count() == 0

        sidebar = page.locator("aside#studio-sidebar")
        sidebar_box = sidebar.bounding_box()
        assert sidebar_box is not None
        assert sidebar_box["y"] <= PIXEL_TOLERANCE, (
            f"Sidebar should start at viewport y=0 with no banner, "
            f"got y={sidebar_box['y']}"
        )

        # After scrolling, the sidebar should still pin to y=0.
        page.evaluate("window.scrollTo(0, 1000)")
        page.wait_for_function(
            """() => {
                const sidebar = document.querySelector('aside#studio-sidebar');
                return sidebar && sidebar.getBoundingClientRect().y <= 1.5;
            }"""
        )
        sidebar_box_after = sidebar.bounding_box()
        assert sidebar_box_after is not None
        assert sidebar_box_after["y"] <= PIXEL_TOLERANCE, (
            f"Sidebar should remain at viewport y=0 after scroll, "
            f"got y={sidebar_box_after['y']}"
        )


# ---------------------------------------------------------------------------
# Scenario 3: sidebar nav scrolls independently when its content overflows
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestSidebarScrollsIndependently:
    """When the sidebar nav is taller than the viewport, scrolling the
    sidebar internally must not scroll the page itself."""

    def test_internal_sidebar_scroll_does_not_scroll_page(
        self, django_server, browser
    ):
        _ensure_tiers()
        _create_staff_user("admin@test.com")

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        # A short viewport guarantees the sidebar's nav overflows.
        page.set_viewport_size({"width": 1280, "height": 500})

        page.goto(f"{django_server}/studio/", wait_until="domcontentloaded")

        sidebar_handle = page.locator("aside#studio-sidebar")

        # The sidebar element itself must overflow — confirms the test is
        # exercising the right condition rather than passing trivially.
        scroll_metrics = sidebar_handle.evaluate(
            "el => ({scrollHeight: el.scrollHeight, clientHeight: el.clientHeight})"
        )
        assert scroll_metrics["scrollHeight"] > scroll_metrics["clientHeight"], (
            "Test setup: sidebar should overflow at this viewport height"
        )

        # Scroll the sidebar to its bottom programmatically (mimics a user
        # dragging the inner scrollbar).
        sidebar_handle.evaluate("el => { el.scrollTop = el.scrollHeight; }")
        page.wait_for_function(
            """el => Math.ceil(el.scrollTop + el.clientHeight) >= el.scrollHeight""",
            arg=sidebar_handle.element_handle(),
        )

        # The page itself must not have scrolled.
        page_scroll_y = page.evaluate("window.scrollY")
        assert page_scroll_y == 0, (
            f"Page should not scroll when sidebar scrolls internally, "
            f"got window.scrollY={page_scroll_y}"
        )

        # The last sidebar item ("Back to site") should now be visible inside
        # the sidebar's clip rect.
        back_link = page.locator(
            'aside#studio-sidebar a[href="/"]:has-text("Back to site")'
        )
        assert back_link.count() == 1
        link_box = back_link.bounding_box()
        sidebar_box = sidebar_handle.bounding_box()
        assert link_box is not None and sidebar_box is not None
        # The link's top is within the sidebar's visible rect.
        assert link_box["y"] >= sidebar_box["y"] - PIXEL_TOLERANCE
        assert (
            link_box["y"] + link_box["height"]
            <= sidebar_box["y"] + sidebar_box["height"] + PIXEL_TOLERANCE
        )


# ---------------------------------------------------------------------------
# Scenario 4: mobile drawer toggle still works after the layout change
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestMobileDrawerToggle:
    """The hamburger button still opens and closes the sidebar on mobile."""

    def test_mobile_toggle_opens_and_closes_drawer(
        self, django_server, browser
    ):
        _ensure_tiers()
        _create_staff_user("admin@test.com")

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        page.set_viewport_size({"width": 390, "height": 844})

        page.goto(f"{django_server}/studio/", wait_until="domcontentloaded")

        sidebar = page.locator("aside#studio-sidebar")
        backdrop = page.locator("#studio-backdrop")

        def has_class(class_str, name):
            # Match a Tailwind class name as a whole token. Substring match
            # would falsely flag e.g. ``md:hidden`` when looking for ``hidden``.
            return name in (class_str or "").split()

        # Initially hidden on mobile (visually hidden via the bare ``hidden``
        # class — both sidebar and backdrop start collapsed).
        assert has_class(sidebar.get_attribute("class"), "hidden")
        assert has_class(backdrop.get_attribute("class"), "hidden")

        # Tap the hamburger toggle.
        page.locator("#studio-sidebar-toggle").click()
        page.wait_for_function(
            """() => {
                const sidebar = document.querySelector('aside#studio-sidebar');
                const backdrop = document.querySelector('#studio-backdrop');
                return sidebar && backdrop
                    && !sidebar.classList.contains('hidden')
                    && !backdrop.classList.contains('hidden');
            }"""
        )

        # Sidebar visible and backdrop visible.
        assert not has_class(sidebar.get_attribute("class"), "hidden")
        assert not has_class(backdrop.get_attribute("class"), "hidden")

        # The sidebar should be pinned to the viewport edges as a drawer
        # (fixed inset-y-0 left-0 on mobile).
        sidebar_box = sidebar.bounding_box()
        assert sidebar_box is not None
        assert sidebar_box["x"] <= PIXEL_TOLERANCE
        assert sidebar_box["y"] <= PIXEL_TOLERANCE

        # Tap a nav link — the click handler should close the drawer.
        page.locator(
            'aside#studio-sidebar a[href="/studio/articles/"]'
        ).click()
        page.wait_for_load_state("domcontentloaded")
        assert "/studio/articles/" in page.url

        sidebar_after = page.locator("aside#studio-sidebar")
        backdrop_after = page.locator("#studio-backdrop")
        assert has_class(sidebar_after.get_attribute("class"), "hidden")
        assert has_class(backdrop_after.get_attribute("class"), "hidden")


# ---------------------------------------------------------------------------
# Scenario 5: no horizontal overflow at common breakpoints
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestNoHorizontalOverflow:
    """The Studio layout must not produce a horizontal scrollbar."""

    @pytest.mark.parametrize(
        "viewport",
        [
            {"width": 390, "height": 844},
            {"width": 768, "height": 1024},
            {"width": 1280, "height": 800},
            {"width": 1920, "height": 1080},
        ],
    )
    def test_no_horizontal_scrollbar(self, django_server, browser, viewport):
        _ensure_tiers()
        _create_staff_user("admin@test.com")

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        page.set_viewport_size(viewport)

        page.goto(
            f"{django_server}/studio/settings",
            wait_until="domcontentloaded",
        )

        scroll_width = page.evaluate("document.documentElement.scrollWidth")
        client_width = page.evaluate("document.documentElement.clientWidth")
        assert scroll_width <= client_width + PIXEL_TOLERANCE, (
            f"Horizontal overflow at {viewport}: "
            f"scrollWidth={scroll_width} > clientWidth={client_width}"
        )
