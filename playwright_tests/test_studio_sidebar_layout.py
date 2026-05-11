"""End-to-end tests for the Studio sidebar layout fixes (#351, #411).

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
  4. Mobile drawer opens, closes, and keeps its header controls separated.
  5. No horizontal overflow at common viewport widths.
"""

import os
from pathlib import Path

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
SCREENSHOT_DIR = Path("/tmp/aisl-issue-411")


def has_class(class_str, name):
    """Match a Tailwind class name as a whole token."""
    return name in (class_str or "").split()


def assert_boxes_do_not_overlap(first, second):
    horizontal_gap = (
        first["x"] + first["width"] <= second["x"] + PIXEL_TOLERANCE
        or second["x"] + second["width"] <= first["x"] + PIXEL_TOLERANCE
    )
    vertical_gap = (
        first["y"] + first["height"] <= second["y"] + PIXEL_TOLERANCE
        or second["y"] + second["height"] <= first["y"] + PIXEL_TOLERANCE
    )
    assert horizontal_gap or vertical_gap, (
        f"Expected boxes not to overlap, got first={first}, second={second}"
    )


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
    """The hamburger, close control, and backdrop work on mobile."""

    def test_mobile_toggle_opens_drawer_without_header_overlap(
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

        # Initially hidden on mobile (visually hidden via the bare ``hidden``
        # class — both sidebar and backdrop start collapsed).
        assert has_class(sidebar.get_attribute("class"), "hidden")
        assert has_class(backdrop.get_attribute("class"), "hidden")
        assert page.locator("#studio-sidebar-toggle").get_attribute(
            "aria-expanded"
        ) == "false"

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
        assert page.locator("#studio-sidebar-toggle").get_attribute(
            "aria-expanded"
        ) == "true"

        # The sidebar should be pinned to the viewport edges as a drawer
        # (fixed inset-y-0 left-0 on mobile).
        sidebar_box = sidebar.bounding_box()
        assert sidebar_box is not None
        assert sidebar_box["x"] <= PIXEL_TOLERANCE
        assert sidebar_box["y"] <= PIXEL_TOLERANCE

        studio_brand = page.locator('aside#studio-sidebar a[href="/studio/"]').first
        close_button = page.locator("#studio-sidebar-close")
        brand_box = studio_brand.bounding_box()
        close_box = close_button.bounding_box()
        assert brand_box is not None and close_box is not None
        assert close_box["width"] >= 44
        assert close_box["height"] >= 44
        assert_boxes_do_not_overlap(brand_box, close_box)
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        page.screenshot(
            path=SCREENSHOT_DIR / "studio-mobile-sidebar-open-390.png",
            full_page=True,
        )

    def test_mobile_close_control_closes_drawer_and_returns_focus(
        self, django_server, browser
    ):
        _ensure_tiers()
        _create_staff_user("admin@test.com")

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        page.set_viewport_size({"width": 390, "height": 844})

        page.goto(f"{django_server}/studio/", wait_until="domcontentloaded")

        toggle = page.locator("#studio-sidebar-toggle")
        sidebar = page.locator("aside#studio-sidebar")
        backdrop = page.locator("#studio-backdrop")

        toggle.click()
        page.locator("#studio-sidebar-close").click()
        page.wait_for_function(
            """() => {
                const sidebar = document.querySelector('aside#studio-sidebar');
                const backdrop = document.querySelector('#studio-backdrop');
                return sidebar && backdrop
                    && sidebar.classList.contains('hidden')
                    && backdrop.classList.contains('hidden');
            }"""
        )

        assert has_class(sidebar.get_attribute("class"), "hidden")
        assert has_class(backdrop.get_attribute("class"), "hidden")
        assert toggle.get_attribute("aria-expanded") == "false"
        assert page.evaluate("document.activeElement.id") == "studio-sidebar-toggle"

    def test_mobile_backdrop_closes_drawer(self, django_server, browser):
        _ensure_tiers()
        _create_staff_user("admin@test.com")

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        page.set_viewport_size({"width": 390, "height": 844})

        page.goto(f"{django_server}/studio/", wait_until="domcontentloaded")

        sidebar = page.locator("aside#studio-sidebar")
        backdrop = page.locator("#studio-backdrop")

        page.locator("#studio-sidebar-toggle").click()
        backdrop.click(position={"x": 340, "y": 100})
        page.wait_for_function(
            """() => {
                const sidebar = document.querySelector('aside#studio-sidebar');
                const backdrop = document.querySelector('#studio-backdrop');
                return sidebar && backdrop
                    && sidebar.classList.contains('hidden')
                    && backdrop.classList.contains('hidden');
            }"""
        )

        assert has_class(sidebar.get_attribute("class"), "hidden")
        assert has_class(backdrop.get_attribute("class"), "hidden")

    def test_mobile_nav_link_closes_drawer_after_scrolling(
        self, django_server, browser
    ):
        _ensure_tiers()
        _create_staff_user("admin@test.com")

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        page.set_viewport_size({"width": 390, "height": 500})

        page.goto(f"{django_server}/studio/", wait_until="domcontentloaded")
        page.locator("#studio-sidebar-toggle").click()

        nav = page.locator("#studio-sidebar-nav")
        settings_link = page.locator(
            'aside#studio-sidebar a[href="/studio/settings/"]'
        )

        nav.evaluate("el => { el.scrollTop = el.scrollHeight; }")
        settings_link.click()
        page.wait_for_load_state("domcontentloaded")

        assert "/studio/settings/" in page.url
        assert has_class(
            page.locator("aside#studio-sidebar").get_attribute("class"),
            "hidden",
        )

    def test_mobile_keyboard_toggles_drawer(self, django_server, browser):
        _ensure_tiers()
        _create_staff_user("admin@test.com")

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        page.set_viewport_size({"width": 390, "height": 844})

        page.goto(f"{django_server}/studio/", wait_until="domcontentloaded")

        toggle = page.locator("#studio-sidebar-toggle")
        toggle.focus()
        page.keyboard.press("Enter")
        page.wait_for_function(
            """() => {
                const sidebar = document.querySelector('aside#studio-sidebar');
                return sidebar && !sidebar.classList.contains('hidden');
            }"""
        )

        assert toggle.get_attribute("aria-expanded") == "true"
        assert page.evaluate("document.activeElement.id") == "studio-sidebar-close"

        page.keyboard.press("Escape")
        page.wait_for_function(
            """() => {
                const sidebar = document.querySelector('aside#studio-sidebar');
                return sidebar && sidebar.classList.contains('hidden');
            }"""
        )
        assert toggle.get_attribute("aria-expanded") == "false"
        assert page.evaluate("document.activeElement.id") == "studio-sidebar-toggle"

        page.keyboard.press("Space")
        page.wait_for_function(
            """() => {
                const sidebar = document.querySelector('aside#studio-sidebar');
                return sidebar && !sidebar.classList.contains('hidden');
            }"""
        )
        assert toggle.get_attribute("aria-expanded") == "true"

    def test_mobile_toggle_opens_and_nav_link_closes_drawer(
        self, django_server, browser
    ):
        _ensure_tiers()
        _create_staff_user("admin@test.com")

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        page.set_viewport_size({"width": 390, "height": 844})

        page.goto(f"{django_server}/studio/", wait_until="domcontentloaded")

        page.locator("#studio-sidebar-toggle").click()
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


@pytest.mark.django_db(transaction=True)
class TestMobileSidebarScrollAffordance:
    """The mobile drawer communicates when more nav is available below."""

    def test_scroll_affordance_is_visible_until_bottom(
        self, django_server, browser
    ):
        _ensure_tiers()
        _create_staff_user("admin@test.com")

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        page.set_viewport_size({"width": 390, "height": 500})

        page.goto(f"{django_server}/studio/", wait_until="domcontentloaded")
        page.locator("#studio-sidebar-toggle").click()

        nav = page.locator("#studio-sidebar-nav")
        affordance = page.locator("#studio-sidebar-scroll-affordance")
        metrics = nav.evaluate(
            "el => ({scrollHeight: el.scrollHeight, clientHeight: el.clientHeight})"
        )
        assert metrics["scrollHeight"] > metrics["clientHeight"]
        assert affordance.is_visible()

        nav.evaluate("el => { el.scrollTop = el.scrollHeight; }")
        page.wait_for_function(
            """() => {
                const affordance = document.querySelector('#studio-sidebar-scroll-affordance');
                return affordance && affordance.classList.contains('hidden');
            }"""
        )
        assert not affordance.is_visible()

    def test_system_links_remain_reachable_after_scrolling(
        self, django_server, browser
    ):
        _ensure_tiers()
        _create_staff_user("admin@test.com")

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        page.set_viewport_size({"width": 390, "height": 500})

        page.goto(f"{django_server}/studio/", wait_until="domcontentloaded")
        page.locator("#studio-sidebar-toggle").click()

        nav = page.locator("#studio-sidebar-nav")
        nav.evaluate("el => { el.scrollTop = el.scrollHeight; }")

        for label in [
            "Content Sync",
            "Worker",
            "Redirects",
            "Announcement",
            "Settings",
        ]:
            link = page.locator(f'aside#studio-sidebar a:has-text("{label}")')
            assert link.count() == 1
            assert link.is_visible()


@pytest.mark.django_db(transaction=True)
class TestSidebarNavOrder:
    """The Studio nav groups and links retain their existing order."""

    def test_sidebar_nav_groups_and_links_stay_in_order(
        self, django_server, browser
    ):
        _ensure_tiers()
        _create_staff_user("admin@test.com")

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        page.set_viewport_size({"width": 1280, "height": 800})

        page.goto(f"{django_server}/studio/", wait_until="domcontentloaded")

        nav_items = page.locator("#studio-sidebar-nav p, #studio-sidebar-nav a")
        actual = [
            item.strip()
            for item in nav_items.all_inner_texts()
            if item.strip() and not item.strip().startswith("v")
        ]
        assert actual == [
            "CONTENT",
            "Courses",
            "Articles",
            "Projects",
            "Recordings",
            "Workshops",
            "Downloads",
            "MEMBERS",
            "CRM",
            "Sprints",
            "Plans",
            "EVENTS & OUTREACH",
            "Events",
            "Campaigns",
            "Notifications",
            "ANALYTICS",
            "UTM Campaigns",
            "UTM Analytics",
            "USERS",
            "Users",
            "Tier Overrides",
            "User imports",
            "New User",
            "SYSTEM",
            "Content Sync",
            "Worker",
            "Redirects",
            "Announcement",
            "Email templates",
            "Settings",
            "API tokens",
            "Back to site",
        ]


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
