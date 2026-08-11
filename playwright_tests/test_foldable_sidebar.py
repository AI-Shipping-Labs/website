"""End-to-end tests for the foldable course-unit sidebar (issue #229).

These exercise the actual JavaScript: collapse, expand, and persistence
of the preference across navigations via localStorage.
"""

import os

import pytest

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_user as _create_user,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection

# Issue #656: this module uses local-only fixtures (DB seeding,
# session-cookie injection, etc.) and cannot run against the
# deployed dev environment. See _docs/testing-guidelines.md.
pytestmark = pytest.mark.local_only


def _clear_courses():
    from content.models import Course

    Course.objects.all().delete()
    connection.close()


def _create_course(title, slug, required_level=0):
    from content.models import Course

    course = Course(
        title=title,
        slug=slug,
        description="",
        required_level=required_level,
        status="published",
    )
    course.save()
    connection.close()
    return course


def _create_module(course, title, sort_order=0):
    from django.utils.text import slugify

    from content.models import Module

    m = Module(
        course=course, title=title, slug=slugify(title), sort_order=sort_order,
    )
    m.save()
    connection.close()
    return m


def _create_unit(module, title, sort_order=0, body=""):
    from django.utils.text import slugify

    from content.models import Unit

    u = Unit(
        module=module,
        title=title,
        slug=slugify(title),
        sort_order=sort_order,
        body=body,
        timestamps=[],
    )
    u.save()
    connection.close()
    return u


def _seed_two_units():
    """Premium course with 2 units. Returns (unit1_url, unit2_url)."""
    _clear_courses()
    _create_user("foldable@test.com", tier_slug="premium")
    course = _create_course(
        title="Foldable Sidebar Course",
        slug="foldable-sidebar-course",
        required_level=30,
    )
    mod = _create_module(course, "Module 1", sort_order=0)
    u1 = _create_unit(
        mod,
        "Lesson One",
        sort_order=0,
        body=(
            "One body\n\n"
            "| Module | Topic |\n"
            "|---|---|\n"
            "| 1 | Introduction |\n"
            "| 2 | Functions |"
        ),
    )
    u2 = _create_unit(mod, "Lesson Two", sort_order=1, body="Two body")
    return u1.get_absolute_url(), u2.get_absolute_url()


@pytest.mark.django_db(transaction=True)
class TestFoldableSidebarToggle:
    """The collapse/expand buttons toggle the layout and persist state."""

    def test_collapse_then_expand_on_desktop(self, django_server, browser):
        u1_url, _ = _seed_two_units()
        context = _auth_context(browser, "foldable@test.com")
        page = context.new_page()
        # Default desktop viewport is 1280x720 from auth_context.
        page.goto(f"{django_server}{u1_url}", wait_until="domcontentloaded")

        # Default state: expanded.
        state = page.evaluate(
            "document.documentElement.getAttribute('data-content-sidebar')"
        )
        assert state == "expanded", f"Expected expanded by default, got {state!r}"

        # Collapse via the in-sidebar button.
        collapse_btn = page.locator('[data-testid="content-sidebar-collapse-btn"]')
        assert collapse_btn.is_visible(), "Collapse button should be visible at lg+"
        collapse_box = collapse_btn.bounding_box()
        assert collapse_box is not None
        collapse_btn.click()

        # After click: collapsed state, localStorage written, floating toggle visible.
        state = page.evaluate(
            "document.documentElement.getAttribute('data-content-sidebar')"
        )
        assert state == "collapsed", f"Expected collapsed after click, got {state!r}"
        stored = page.evaluate("localStorage.getItem('content-sidebar-collapsed')")
        assert stored == "1", f"Expected localStorage=1, got {stored!r}"

        floating = page.locator('[data-testid="content-sidebar-floating-toggle"]')
        page.wait_for_function(
            "getComputedStyle(document.getElementById("
            "'content-sidebar-floating-toggle')).opacity === '1'"
        )
        assert floating.is_visible(), "Floating toggle should appear when collapsed"

        # Aside has computed width 0 (or near it) when collapsed.
        # CSS uses a 200ms transition; poll until the post-transition
        # width is observable instead of sleeping a fixed 400ms (#290).
        page.wait_for_function(
            "document.getElementById('content-sidebar-aside')"
            ".getBoundingClientRect().width <= 1",
            timeout=2000,
        )
        aside_width = page.evaluate(
            "document.getElementById('content-sidebar-aside').getBoundingClientRect().width"
        )
        assert aside_width <= 1, f"Sidebar should be width=0 when collapsed, got {aside_width}"

        floating_box = floating.bounding_box()
        assert floating_box is not None
        assert abs(floating_box["width"] - collapse_box["width"]) <= 1
        assert abs(floating_box["height"] - collapse_box["height"]) <= 1
        assert abs(floating_box["y"] - collapse_box["y"]) <= 1
        assert 15 <= floating_box["x"] <= 17, (
            f"Restore control should float 16px from the viewport edge, got "
            f'{floating_box["x"]}px'
        )

        # A refresh keeps both the collapsed preference and the measured
        # vertical position instead of falling back to a slightly different
        # fixed offset.
        page.reload(wait_until="domcontentloaded")
        floating = page.locator(
            '[data-testid="content-sidebar-floating-toggle"]'
        )
        assert floating.is_visible()
        refreshed_box = floating.bounding_box()
        assert refreshed_box is not None
        assert abs(refreshed_box["y"] - collapse_box["y"]) <= 1
        assert page.locator('[data-reader-breadcrumb]').is_visible()

        # Re-expand via floating toggle.
        floating.click()
        state = page.evaluate(
            "document.documentElement.getAttribute('data-content-sidebar')"
        )
        assert state == "expanded", f"Expected expanded after re-click, got {state!r}"
        stored = page.evaluate("localStorage.getItem('content-sidebar-collapsed')")
        assert stored == "0", f"Expected localStorage=0 after expand, got {stored!r}"

        context.close()

    def test_state_persists_across_navigations(self, django_server, browser):
        """Collapse on unit 1, navigate to unit 2; sidebar should still be collapsed."""
        u1_url, u2_url = _seed_two_units()
        context = _auth_context(browser, "foldable@test.com")
        page = context.new_page()
        page.goto(f"{django_server}{u1_url}", wait_until="domcontentloaded")

        # Collapse it.
        page.locator('[data-testid="content-sidebar-collapse-btn"]').click()
        state = page.evaluate(
            "document.documentElement.getAttribute('data-content-sidebar')"
        )
        assert state == "collapsed"

        # Navigate to unit 2 via direct goto (full page reload).
        page.goto(f"{django_server}{u2_url}", wait_until="domcontentloaded")

        # Collapsed state should persist (no flash of expanded sidebar).
        state = page.evaluate(
            "document.documentElement.getAttribute('data-content-sidebar')"
        )
        assert state == "collapsed", "Collapse state should persist across page loads"
        floating = page.locator('[data-testid="content-sidebar-floating-toggle"]')
        assert floating.is_visible()
        aside_width = page.evaluate(
            "document.getElementById('content-sidebar-aside').getBoundingClientRect().width"
        )
        assert aside_width <= 1, f"Sidebar should still be collapsed after nav, got {aside_width}"

        context.close()

    def test_default_state_is_expanded_for_new_visitor(self, django_server, browser):
        """A fresh browser context with no localStorage shows the sidebar."""
        u1_url, _ = _seed_two_units()
        context = _auth_context(browser, "foldable@test.com")
        page = context.new_page()
        page.goto(f"{django_server}{u1_url}", wait_until="domcontentloaded")

        # No localStorage entry yet -> default expanded.
        stored = page.evaluate("localStorage.getItem('content-sidebar-collapsed')")
        assert stored in (None, "0"), (
            f"Default should be no entry or '0', got {stored!r}"
        )
        state = page.evaluate(
            "document.documentElement.getAttribute('data-content-sidebar')"
        )
        assert state == "expanded"

        # Sidebar visible with non-zero width.
        aside_width = page.evaluate(
            "document.getElementById('content-sidebar-aside').getBoundingClientRect().width"
        )
        assert aside_width > 100, f"Sidebar should be visible by default, got width {aside_width}"

        context.close()

    def test_main_content_stays_blog_width_when_collapsed(
        self, django_server, browser,
    ):
        """The main column stays at max-w-3xl in both sidebar states."""
        u1_url, _ = _seed_two_units()
        context = _auth_context(browser, "foldable@test.com")
        page = context.new_page()
        page.goto(f"{django_server}{u1_url}", wait_until="domcontentloaded")

        main = page.locator('#content-sidebar-main')
        max_width_before = page.evaluate(
            "getComputedStyle(document.getElementById('content-sidebar-main')).maxWidth"
        )
        assert max_width_before == "768px"
        box_before = main.bounding_box()
        assert box_before is not None
        assert 767 <= box_before["width"] <= 769

        page.locator('[data-testid="content-sidebar-collapse-btn"]').click()

        # After collapse: max-width matches the blog reader width (48rem).
        max_width_after = page.evaluate(
            "getComputedStyle(document.getElementById('content-sidebar-main')).maxWidth"
        )
        assert "px" in max_width_after, f"Expected pixel max-width, got {max_width_after!r}"
        # 48rem at the default 16px root font = 768px (Tailwind max-w-3xl).
        px = float(max_width_after.replace("px", ""))
        assert 767 <= px <= 769, f"Expected 768px max-width, got {px}"
        box_after = main.bounding_box()
        assert box_after is not None
        assert abs(box_after["width"] - box_before["width"]) <= 1, (
            "Reader width changed while collapsing the sidebar"
        )

        context.close()

    def test_course_reader_centers_markdown_tables(self, django_server, browser):
        """Intrinsic-width lesson tables are centered in the reader prose."""
        u1_url, _ = _seed_two_units()
        context = _auth_context(browser, "foldable@test.com")
        page = context.new_page()
        page.goto(f"{django_server}{u1_url}", wait_until="domcontentloaded")

        prose = page.locator('[data-testid="course-unit-body"]')
        table = prose.locator("table")
        prose_box = prose.bounding_box()
        table_box = table.bounding_box()
        assert prose_box is not None and table_box is not None
        assert table_box["width"] < prose_box["width"]

        prose_center = prose_box["x"] + prose_box["width"] / 2
        table_center = table_box["x"] + table_box["width"] / 2
        assert abs(prose_center - table_center) <= 1, (
            f"Table center {table_center} does not match prose center "
            f"{prose_center}"
        )

        context.close()

    def test_mobile_does_not_show_desktop_toggles(self, django_server, browser):
        """At mobile width the desktop collapse/floating buttons are hidden;
        the existing hamburger pattern still works."""
        u1_url, _ = _seed_two_units()
        context = browser.new_context(
            viewport={"width": 375, "height": 800},
        )
        # Authenticate manually since auth_context uses default viewport.
        from playwright_tests.conftest import create_session_for_user

        session_key = create_session_for_user("foldable@test.com")
        context.add_cookies([
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
        page = context.new_page()
        page.goto(f"{django_server}{u1_url}", wait_until="domcontentloaded")

        # Desktop collapse button must not be visible at 375px.
        collapse_btn = page.locator('[data-testid="content-sidebar-collapse-btn"]')
        assert collapse_btn.count() == 1, "Element exists in DOM"
        assert not collapse_btn.is_visible(), "Should be hidden at mobile width"

        floating_btn = page.locator('[data-testid="content-sidebar-floating-toggle"]')
        assert not floating_btn.is_visible(), "Floating toggle must be hidden on mobile"

        # The existing hamburger (#sidebar-toggle-btn) is still functional.
        hamburger = page.locator('#sidebar-toggle-btn')
        assert hamburger.is_visible(), "Mobile hamburger must remain visible"

        context.close()
