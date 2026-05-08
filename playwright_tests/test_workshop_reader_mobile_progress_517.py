"""Playwright E2E tests for issue #517.

Workshop tutorial reader on mobile (Pixel 7 393x851) is missing the
left sidebar / progress indicator. This issue surfaces a new
mobile-only progress bar above the breadcrumb that wraps the existing
``#sidebar-toggle-btn`` (now lifted out of the persistent sidebar
``<aside>``) and adds a ``Page N of M`` / ``Lesson N of M`` text plus
a thin completion fill bar. The change is also applied to the course
unit reader for parity.

Scenarios covered (9):

1. Mobile workshop reader shows "Page 3 of 5" without scrolling and a
   2/5-filled green progress bar.
2. Free user opens the drawer from the top of the page; current page is
   ``aria-current="page"``; no completion glyphs render.
3. Reader navigates between pages from the drawer; new page also shows
   the bar with the new position; drawer collapses by default.
4. Drawer reflects completion immediately after marking complete;
   completion persists after reload and the fill bar updates to ~20%.
5. Anonymous visitor sees position text + drawer toggle but no fill bar
   (no completion data to show without auth).
6. Gated tutorial page does NOT render the mobile progress bar; the
   page-level paywall card is the only chrome alongside title +
   breadcrumb.
7. Course unit reader gets the same chrome with "Lesson 6 of 9".
8. Desktop layout (1280x900) hides the new bar (`lg:hidden`); the
   left sidebar with the full page list is visible; floating-toggle /
   collapse-toggle behaviour still works (regression vs #309 / #483).
9. Single-page workshop renders "Page 1 of 1"; drawer toggle still
   renders for consistency; opening it shows one row marked
   ``aria-current="page"``.

Usage:
    uv run pytest playwright_tests/test_workshop_reader_mobile_progress_517.py -v
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

from playwright_tests.conftest import (  # noqa: E402
    create_session_for_user,
    create_user,
)
from playwright_tests.test_reader_mobile_483 import (  # noqa: E402
    _clear_courses,
    _create_course,
    _create_module,
    _create_unit,
)
from playwright_tests.test_workshops import (  # noqa: E402
    _clear_workshops,
    _create_workshop,
)

SCREENSHOT_DIR = Path("/tmp/issue-517-screenshots")
PIXEL_7 = {"width": 393, "height": 851}
DESKTOP = {"width": 1280, "height": 900}


def _shot(page, name):
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SCREENSHOT_DIR / f"{name}.png"), full_page=True)


def _mobile_context(browser, email):
    """Authenticated context at Pixel 7 393x851."""
    session_key = create_session_for_user(email)
    ctx = browser.new_context(viewport=PIXEL_7)
    ctx.add_cookies([
        {
            "name": "sessionid",
            "value": session_key,
            "domain": "127.0.0.1",
            "path": "/",
        },
        {
            "name": "csrftoken",
            "value": "e2e-test-csrf-token-value",
            "domain": "127.0.0.1",
            "path": "/",
        },
    ])
    return ctx


def _mobile_anon_context(browser):
    """Anonymous context at Pixel 7 393x851."""
    return browser.new_context(viewport=PIXEL_7)


def _seed_5_page_workshop(slug="regional-setup", pages=0):
    """Seed a workshop with 5 tutorial pages."""
    _clear_workshops()
    return _create_workshop(
        slug=slug,
        title="Regional Setup",
        landing=0,
        pages=pages,
        recording=20,
        pages_data=[
            ("intro", "Introduction", "# Intro\n\nIntro body."),
            ("accounts", "Accounts", "# Accounts\n\nAccounts body."),
            ("regional-setup", "Regional Setup", "# Regional\n\nRegional body."),
            ("cli", "CLI", "# CLI\n\nCLI body."),
            ("verify", "Verify", "# Verify\n\nVerify body."),
        ],
    )


# ----------------------------------------------------------------------
# Scenario 1: Mobile workshop reader shows position at a glance.
# ----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestMobileProgressBarPositionAtAGlance:
    def test_progress_text_and_fill_visible_above_breadcrumb(
        self, browser, django_server,
    ):
        _seed_5_page_workshop()
        create_user("main-517@test.com", tier_slug="main")
        # Mark first 2 pages completed via the API so the fill bar
        # reflects 2/5 ≈ 40%.
        from accounts.models import User
        from content.models import WorkshopPage
        from content.services import completion as completion_service
        user = User.objects.get(email="main-517@test.com")
        for slug in ("intro", "accounts"):
            page_obj = WorkshopPage.objects.get(
                workshop__slug="regional-setup", slug=slug,
            )
            completion_service.mark_completed(user, page_obj)
        from django.db import connection
        connection.close()

        ctx = _mobile_context(browser, "main-517@test.com")
        page = ctx.new_page()
        try:
            page.goto(
                f"{django_server}/workshops/regional-setup/tutorial/regional-setup",
                wait_until="domcontentloaded",
            )
            bar = page.locator('[data-testid="reader-mobile-progress-bar"]')
            bar.wait_for(state="visible")

            # The bar is above the breadcrumb (no scroll required).
            bar_box = bar.bounding_box()
            crumb = page.locator('[data-testid="page-breadcrumb"]')
            crumb_box = crumb.bounding_box()
            assert bar_box is not None and crumb_box is not None
            assert bar_box["y"] < crumb_box["y"], (
                f"Progress bar y={bar_box['y']} should be above "
                f"breadcrumb y={crumb_box['y']}"
            )
            # No-scroll requirement: the bar must be on-screen at first
            # paint (Pixel 7 height = 851px).
            assert bar_box["y"] + bar_box["height"] < 851, (
                f"Progress bar bottom edge {bar_box['y'] + bar_box['height']}"
                f" exceeds viewport height 851 — user has to scroll."
            )

            # Position text reads "Page 3 of 5".
            text = page.locator(
                '[data-testid="reader-mobile-progress-text"]',
            )
            assert text.text_content().strip() == "Page 3 of 5"

            # Fill bar at ~40% width.
            fill = page.locator('[data-testid="reader-mobile-progress-fill"]')
            fill_box = fill.bounding_box()
            track_box = fill.evaluate_handle(
                "el => el.parentElement"
            ).as_element().bounding_box()
            assert fill_box is not None and track_box is not None
            ratio = fill_box["width"] / track_box["width"]
            assert 0.35 < ratio < 0.45, (
                f"Fill ratio {ratio:.3f} outside the expected ~0.40"
            )

            _shot(page, "01-position-at-a-glance")
        finally:
            ctx.close()


# ----------------------------------------------------------------------
# Scenario 2: Free user opens the drawer from the top of the page.
# ----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestFreeUserOpensDrawerFromTop:
    def test_drawer_toggle_visible_and_lists_pages_with_aria_current(
        self, browser, django_server,
    ):
        # Public-tour workshop: pages_required_level=0 so a free user
        # has access to the body (no paywall).
        _clear_workshops()
        _create_workshop(
            slug="public-tour",
            title="Public Tour",
            landing=0,
            pages=0,
            recording=20,
            pages_data=[
                ("intro", "Intro", "# Intro\n\n."),
                ("step-2", "Step 2", "# Step 2\n\n."),
                ("step-3", "Step 3", "# Step 3\n\n."),
            ],
        )
        create_user("free-517@test.com", tier_slug="free")

        ctx = _mobile_context(browser, "free-517@test.com")
        page = ctx.new_page()
        try:
            page.goto(
                f"{django_server}/workshops/public-tour/tutorial/intro",
                wait_until="domcontentloaded",
            )
            toggle = page.locator(
                '[data-testid="reader-mobile-drawer-toggle"]',
            )
            toggle.wait_for(state="visible")

            # Drawer initially collapsed.
            nav = page.locator("#sidebar-nav")
            assert nav.is_hidden()

            toggle.click()
            nav.wait_for(state="visible")

            # Current page marked aria-current="page".
            current = page.locator(
                '#sidebar-nav a[aria-current="page"]',
            )
            current.wait_for(state="visible")
            assert "Intro" in current.text_content()

            # No green check-circle — user has no completions.
            completed = page.locator(
                '#sidebar-nav [data-testid="sidebar-completed-page"]',
            )
            assert completed.count() == 0

            _shot(page, "02-drawer-open-free-user")

            # Tap the toggle again to collapse.
            toggle.click()
            nav.wait_for(state="hidden")
        finally:
            ctx.close()


# ----------------------------------------------------------------------
# Scenario 3: Reader navigates between pages from the drawer.
# ----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestNavigateBetweenPagesFromDrawer:
    def test_drawer_navigation_updates_progress_text(
        self, browser, django_server,
    ):
        _seed_5_page_workshop()
        create_user("nav-517@test.com", tier_slug="main")

        ctx = _mobile_context(browser, "nav-517@test.com")
        page = ctx.new_page()
        try:
            page.goto(
                f"{django_server}/workshops/regional-setup/tutorial/intro",
                wait_until="domcontentloaded",
            )
            toggle = page.locator(
                '[data-testid="reader-mobile-drawer-toggle"]',
            )
            toggle.click()
            page.locator("#sidebar-nav").wait_for(state="visible")

            # Click the CLI page link inside the drawer.
            page.locator(
                '#sidebar-nav a[href="/workshops/regional-setup/tutorial/cli"]',
            ).click()
            page.wait_for_url("**/workshops/regional-setup/tutorial/cli")

            text = page.locator(
                '[data-testid="reader-mobile-progress-text"]',
            )
            text.wait_for(state="visible")
            assert text.text_content().strip() == "Page 4 of 5"

            # Drawer collapsed by default after navigation.
            assert page.locator("#sidebar-nav").is_hidden()

            _shot(page, "03-after-drawer-navigation")
        finally:
            ctx.close()


# ----------------------------------------------------------------------
# Scenario 4: Drawer reflects completion immediately after marking complete.
# ----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestDrawerReflectsCompletionAfterReload:
    def test_completion_persists_and_fill_bar_updates_after_reload(
        self, browser, django_server,
    ):
        _seed_5_page_workshop()
        create_user("complete-517@test.com", tier_slug="main")

        ctx = _mobile_context(browser, "complete-517@test.com")
        page = ctx.new_page()
        try:
            page.goto(
                f"{django_server}/workshops/regional-setup/tutorial/intro",
                wait_until="domcontentloaded",
            )
            # Tap mark-complete (button copy: "Mark as completed").
            # Issue #483 introduced two completion buttons in the DOM:
            # the mobile-only `*-mobile` variant (visible at <sm) and
            # the desktop-inline variant (visible at sm+). On Pixel 7
            # (393px) we click the mobile one.
            page.locator(
                '[data-testid="mark-page-complete-btn-mobile"]',
            ).click()
            page.wait_for_function(
                "document.querySelector("
                "'[data-testid=\"mark-page-complete-btn-mobile\"]')"
                ".textContent.includes('Completed')",
            )

            # Reload so the server re-renders the drawer with the new
            # completion. The button is updated client-side via
            # `_scripts.html` but the sidebar list is server-rendered.
            page.reload(wait_until="domcontentloaded")

            # The bar reads "Page 1 of 5" still and the fill bar
            # reflects 1/5 = 20%.
            text = page.locator(
                '[data-testid="reader-mobile-progress-text"]',
            )
            assert text.text_content().strip() == "Page 1 of 5"

            fill = page.locator(
                '[data-testid="reader-mobile-progress-fill"]',
            )
            fill_box = fill.bounding_box()
            track_box = fill.evaluate_handle(
                "el => el.parentElement"
            ).as_element().bounding_box()
            ratio = fill_box["width"] / track_box["width"]
            assert 0.15 < ratio < 0.25, (
                f"Fill ratio {ratio:.3f} outside the expected ~0.20"
            )

            # Drawer shows the intro row as completed after reload.
            page.locator(
                '[data-testid="reader-mobile-drawer-toggle"]',
            ).click()
            page.locator("#sidebar-nav").wait_for(state="visible")
            # Wait for lucide to rewrite the <i> tags to <svg>; the
            # data-testid attribute is preserved on the resulting svg.
            page.wait_for_function(
                "document.querySelectorAll("
                "'#sidebar-nav [data-testid=\"sidebar-completed-page\"]'"
                ").length > 0",
                timeout=2000,
            )
            assert page.locator(
                '#sidebar-nav [data-testid="sidebar-completed-page"]',
            ).count() == 1

            _shot(page, "04-completion-after-reload")
        finally:
            ctx.close()


# ----------------------------------------------------------------------
# Scenario 5: Anonymous visitor sees position but not completion fill.
# ----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestAnonymousVisitorSeesPositionWithoutFill:
    def test_anonymous_sees_text_and_toggle_without_fill_bar(
        self, browser, django_server,
    ):
        _clear_workshops()
        _create_workshop(
            slug="public-tour",
            title="Public Tour",
            landing=0,
            pages=0,  # publicly accessible body
            recording=20,
            pages_data=[
                ("intro", "Intro", "# Intro\n\n."),
                ("step-2", "Step 2", "# Step 2\n\n."),
                ("step-3", "Step 3", "# Step 3\n\n."),
            ],
        )

        ctx = _mobile_anon_context(browser)
        page = ctx.new_page()
        try:
            page.goto(
                f"{django_server}/workshops/public-tour/tutorial/step-2",
                wait_until="domcontentloaded",
            )
            text = page.locator(
                '[data-testid="reader-mobile-progress-text"]',
            )
            text.wait_for(state="visible")
            assert text.text_content().strip() == "Page 2 of 3"

            # Drawer toggle works (anonymous).
            toggle = page.locator(
                '[data-testid="reader-mobile-drawer-toggle"]',
            )
            toggle.click()
            page.locator("#sidebar-nav").wait_for(state="visible")

            # NO fill bar (no completion data to display for anonymous).
            assert page.locator(
                '[data-testid="reader-mobile-progress-fill"]',
            ).count() == 0

            _shot(page, "05-anonymous-no-fill")
        finally:
            ctx.close()


# ----------------------------------------------------------------------
# Scenario 6: Gated tutorial page does not show the mobile progress bar.
# ----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestGatedPageHidesProgressBar:
    def test_free_user_on_paid_workshop_sees_paywall_not_progress_bar(
        self, browser, django_server,
    ):
        _clear_workshops()
        _create_workshop(
            slug="main-only",
            title="Main Only",
            landing=0,
            pages=20,  # Main-tier wall
            recording=20,
            pages_data=[
                ("intro", "Intro", "Long body. " * 50),
                ("next", "Next", "."),
            ],
        )
        create_user("free-gate-517@test.com", tier_slug="free")

        ctx = _mobile_context(browser, "free-gate-517@test.com")
        page = ctx.new_page()
        try:
            response = page.goto(
                f"{django_server}/workshops/main-only/tutorial/intro",
                wait_until="domcontentloaded",
            )
            assert response.status == 403

            # The paywall card is rendered.
            paywall = page.locator('[data-testid="page-paywall"]')
            paywall.wait_for(state="visible")

            # The mobile progress bar is NOT in the DOM.
            assert page.locator(
                '[data-testid="reader-mobile-progress-bar"]',
            ).count() == 0

            # Title + breadcrumb still visible.
            assert page.locator(
                '[data-testid="page-title"]',
            ).is_visible()
            assert page.locator(
                '[data-testid="page-breadcrumb"]',
            ).is_visible()

            _shot(page, "06-gated-no-progress-bar")
        finally:
            ctx.close()


# ----------------------------------------------------------------------
# Scenario 7: Course unit reader gets the same mobile chrome.
# ----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestCourseUnitReaderParity:
    def test_course_unit_shows_lesson_n_of_m_with_completion_glyphs(
        self, browser, django_server,
    ):
        _clear_courses()
        course = _create_course(
            title="Intro to LLMs",
            slug="intro-to-llms",
            required_level=0,
        )
        # 3 modules with 4, 3, 2 units = 9 total. Target = module 2 / unit 2
        # which is the 6th overall unit.
        m1 = _create_module(course, "Module 1", sort_order=1)
        for i in range(1, 5):
            _create_unit(m1, f"M1U{i}", sort_order=i, body=".")
        m2 = _create_module(course, "Module 2", sort_order=2)
        for i in range(1, 4):
            _create_unit(m2, f"M2U{i}", sort_order=i, body=".")
        m3 = _create_module(course, "Module 3", sort_order=3)
        for i in range(1, 3):
            _create_unit(m3, f"M3U{i}", sort_order=i, body=".")

        create_user("main-course-517@test.com", tier_slug="main")
        # Mark M2U1 + M3U1 (2 of 9 = ~22%) completed via the API.
        from django.db import connection

        from accounts.models import User
        from content.models import Unit
        from content.services import completion as completion_service
        u = User.objects.get(email="main-course-517@test.com")
        completion_service.mark_completed(
            u, Unit.objects.get(module=m2, slug="m2u1"),
        )
        completion_service.mark_completed(
            u, Unit.objects.get(module=m3, slug="m3u1"),
        )
        connection.close()

        ctx = _mobile_context(browser, "main-course-517@test.com")
        page = ctx.new_page()
        try:
            # Navigate to module 2 / unit 2 (6th overall).
            page.goto(
                f"{django_server}/courses/intro-to-llms/module-2/m2u2",
                wait_until="domcontentloaded",
            )
            text = page.locator(
                '[data-testid="reader-mobile-progress-text"]',
            )
            text.wait_for(state="visible")
            assert text.text_content().strip() == "Lesson 6 of 9"

            # Open drawer; module 2 is expanded by default (current
            # module). Verify two completion glyphs appear in the drawer.
            page.locator(
                '[data-testid="reader-mobile-drawer-toggle"]',
            ).click()
            page.locator("#sidebar-nav").wait_for(state="visible")
            # Force every <details> in the drawer open so we can count
            # completion glyphs across all modules without interacting
            # with each summary.
            page.evaluate(
                "document.querySelectorAll('#sidebar-nav details')"
                ".forEach(d => d.open = true)"
            )
            # Wait for lucide to rewrite the placeholder <i> tags into
            # <svg> elements; the rewrite copies the class list to the
            # svg, so the green check-circles end up as
            # ``svg.lucide-check-circle-2`` with ``text-green-400``.
            page.wait_for_function(
                "document.querySelectorAll('#sidebar-nav svg').length > 0",
                timeout=2000,
            )
            completed_count = page.locator(
                '#sidebar-nav svg.lucide-check-circle-2'
            ).count()
            assert completed_count == 2, (
                f"Expected 2 completion glyphs in drawer, got "
                f"{completed_count}"
            )

            _shot(page, "07-course-unit-parity")
        finally:
            ctx.close()


# ----------------------------------------------------------------------
# Scenario 8: Desktop layout is unchanged (regression vs #309 / #483).
# ----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestDesktopLayoutUnchanged:
    def test_desktop_hides_mobile_bar_and_keeps_left_sidebar(
        self, browser, django_server,
    ):
        _seed_5_page_workshop()
        create_user("desktop-517@test.com", tier_slug="main")

        session_key = create_session_for_user("desktop-517@test.com")
        ctx = browser.new_context(viewport=DESKTOP)
        ctx.add_cookies([
            {
                "name": "sessionid",
                "value": session_key,
                "domain": "127.0.0.1",
                "path": "/",
            },
            {
                "name": "csrftoken",
                "value": "e2e-test-csrf-token-value",
                "domain": "127.0.0.1",
                "path": "/",
            },
        ])
        page = ctx.new_page()
        try:
            page.goto(
                f"{django_server}/workshops/regional-setup/tutorial/regional-setup",
                wait_until="domcontentloaded",
            )

            # The mobile bar is in the DOM but hidden by `lg:hidden`.
            bar = page.locator(
                '[data-testid="reader-mobile-progress-bar"]',
            )
            assert bar.count() == 1
            assert bar.is_hidden(), (
                "Mobile bar should be hidden via lg:hidden on a 1280px "
                "desktop viewport."
            )

            # Left sidebar with the page list is visible.
            nav = page.locator("#sidebar-nav")
            nav.wait_for(state="visible")
            assert nav.locator("a").count() == 5

            # Collapse-toggle still works (regression vs #309 / #483).
            collapse = page.locator(
                '[data-testid="content-sidebar-collapse-btn"]',
            )
            collapse.wait_for(state="visible")
            collapse.click()
            page.wait_for_function(
                "document.documentElement.getAttribute("
                "'data-content-sidebar') === 'collapsed'",
            )
            floating = page.locator(
                '[data-testid="content-sidebar-floating-toggle"]',
            )
            assert floating.is_visible()

            _shot(page, "08-desktop-unchanged")
        finally:
            ctx.close()


# ----------------------------------------------------------------------
# Scenario 9: Single-page workshop renders progress bar correctly.
# ----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestSinglePageWorkshop:
    def test_one_of_one_progress_with_drawer_still_rendered(
        self, browser, django_server,
    ):
        _clear_workshops()
        _create_workshop(
            slug="single",
            title="Single",
            landing=0,
            pages=0,
            recording=20,
            pages_data=[
                ("only", "The Only Page", "# Only\n\nbody"),
            ],
        )
        create_user("single-517@test.com", tier_slug="main")

        ctx = _mobile_context(browser, "single-517@test.com")
        page = ctx.new_page()
        try:
            page.goto(
                f"{django_server}/workshops/single/tutorial/only",
                wait_until="domcontentloaded",
            )
            text = page.locator(
                '[data-testid="reader-mobile-progress-text"]',
            )
            text.wait_for(state="visible")
            assert text.text_content().strip() == "Page 1 of 1"

            toggle = page.locator(
                '[data-testid="reader-mobile-drawer-toggle"]',
            )
            assert toggle.is_visible()
            toggle.click()
            page.locator("#sidebar-nav").wait_for(state="visible")
            current = page.locator(
                '#sidebar-nav a[aria-current="page"]',
            )
            current.wait_for(state="visible")
            assert current.count() == 1

            _shot(page, "09-single-page-workshop")
        finally:
            ctx.close()
