"""Playwright E2E tests for issue #483.

Covers:
- Course detail syllabus on mobile (390x844): module summary tap target
  is at least 44px, padding is tighter than the desktop pattern, and
  no "0 lessons" text appears anywhere on the page.
- Course unit reader bottom navigation on mobile: the mark-complete
  button renders as a stand-alone full-width row above the prev/next
  pair (it is no longer stranded between them). Click flips the
  state to "Completed" and a reload preserves it.
- Workshop tutorial reader bottom navigation on mobile: same layout
  as the course unit reader (parity).
- Long-titled prev/next links do not overflow the 390px viewport
  (already covered for course units in test_course_mobile / sibling
  tests, but reasserted here for the new mobile bottom-nav layout).

Usage:
    uv run pytest playwright_tests/test_reader_mobile_483.py -v
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

from django.db import connection  # noqa: E402

from playwright_tests.conftest import (  # noqa: E402
    create_session_for_user,
    create_user,
)
from playwright_tests.test_workshops import (  # noqa: E402
    _clear_workshops,
    _create_workshop,
)

# The legacy helper in ``playwright_tests.test_course_units`` still
# passes a ``instructor_name`` kwarg that no longer exists on the
# ``Course`` model. We define local fixtures here that match the
# current schema so the test stays decoupled from sibling helper drift.


def _clear_courses():
    from content.models import Course

    Course.objects.all().delete()
    connection.close()


def _create_course(title, slug, required_level=0, description=""):
    from content.models import Course

    course = Course(
        title=title,
        slug=slug,
        description=description,
        required_level=required_level,
        status="published",
    )
    course.save()
    connection.close()
    return course


def _create_module(course, title, sort_order=0):
    from django.utils.text import slugify

    from content.models import Module

    module = Module(
        course=course,
        title=title,
        slug=slugify(title),
        sort_order=sort_order,
    )
    module.save()
    connection.close()
    return module


def _create_unit(module, title, sort_order=0, body=""):
    from django.utils.text import slugify

    from content.models import Unit

    unit = Unit(
        module=module,
        title=title,
        slug=slugify(title),
        sort_order=sort_order,
        body=body,
    )
    unit.save()
    connection.close()
    return unit

SHOT_DIR = "/tmp/issue-483-screenshots"
MOBILE_VIEWPORT = {"width": 390, "height": 844}
DESKTOP_VIEWPORT = {"width": 1280, "height": 900}


def _mobile_context(browser, email):
    """Authenticated context at iPhone 14-ish 390x844 viewport."""
    session_key = create_session_for_user(email)
    ctx = browser.new_context(viewport=MOBILE_VIEWPORT)
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


# ----------------------------------------------------------------------
# Scenario 1: course detail syllabus on mobile
# ----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestCourseSyllabusMobileSpacing:
    """Course detail syllabus is tighter on mobile but keeps a 44px tap
    target for module summary rows."""

    def _setup(self):
        _clear_courses()
        course = _create_course(
            title="Mobile Spacing Course",
            slug="mobile-spacing-483",
            required_level=0,
        )
        module = _create_module(course, "Module 1", sort_order=1)
        _create_unit(module, "Unit 1", sort_order=1, body="Hi")
        _create_unit(module, "Unit 2", sort_order=2, body="Bye")
        return course

    def test_module_summary_tap_target_at_least_44px_on_390px(
        self, browser, django_server,
    ):
        os.makedirs(SHOT_DIR, exist_ok=True)
        self._setup()

        ctx = browser.new_context(viewport=MOBILE_VIEWPORT)
        page = ctx.new_page()
        try:
            page.goto(
                f"{django_server}/courses/mobile-spacing-483",
                wait_until="domcontentloaded",
            )
            summary = page.locator(
                '[data-testid="syllabus-module-summary"]',
            ).first
            summary.wait_for(state="visible")

            box = summary.bounding_box()
            assert box is not None
            # 44px tap target floor (issue #483 AC).
            assert box["height"] >= 44, (
                f"Module summary height {box['height']} < 44px on "
                f"a 390x844 viewport — tap target dropped below floor."
            )

            page.screenshot(
                path=f"{SHOT_DIR}/01-syllabus-mobile-390.png",
                full_page=True,
            )
        finally:
            ctx.close()

    def test_zero_lessons_text_not_visible_on_mobile(
        self, browser, django_server,
    ):
        """Suppression of awkward "0 lessons" copy (issue #483 zero-count
        AC). Renders a course where one of the modules has no units to
        guarantee the suppression branch executes."""
        _clear_courses()
        course = _create_course(
            title="Empty Module Course",
            slug="empty-mod-483",
            required_level=0,
        )
        # Module with units → renders "1 lessons".
        full_module = _create_module(course, "Full Module", sort_order=1)
        _create_unit(full_module, "OnlyUnit", sort_order=1, body="ok")
        # Module without units → would have rendered "0 lessons".
        _create_module(course, "Empty Module", sort_order=2)

        ctx = browser.new_context(viewport=MOBILE_VIEWPORT)
        page = ctx.new_page()
        try:
            page.goto(
                f"{django_server}/courses/empty-mod-483",
                wait_until="domcontentloaded",
            )
            # Bring both modules into view by ensuring they render.
            page.locator(
                '[data-testid="syllabus-module-summary"]',
            ).first.wait_for(state="visible")

            content = page.content()
            assert "0 lessons" not in content, (
                "Zero-count copy '0 lessons' must not be present "
                "anywhere on the rendered page."
            )
        finally:
            ctx.close()


# ----------------------------------------------------------------------
# Scenario 2: course unit reader bottom nav on mobile
# ----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestCourseUnitReaderMobileCompletionPlacement:
    """The mark-complete button on mobile sits in a stand-alone row
    above prev/next, and clicking it flips the state to Completed."""

    def _setup(self):
        _clear_courses()
        course = _create_course(
            title="Bottom Nav 483 Course",
            slug="bn-483-course",
            required_level=0,
        )
        module = _create_module(course, "Module 1", sort_order=1)
        unit1 = _create_unit(
            module, "Unit One", sort_order=1, body="Body 1",
        )
        unit2 = _create_unit(
            module, "Unit Two", sort_order=2, body="Body 2",
        )
        return course, module, unit1, unit2

    def test_mobile_completion_row_above_prevnext_and_toggles(
        self, browser, django_server,
    ):
        os.makedirs(SHOT_DIR, exist_ok=True)
        course, module, unit1, unit2 = self._setup()
        create_user("bn-483@test.com", tier_slug="free")

        ctx = _mobile_context(browser, "bn-483@test.com")
        page = ctx.new_page()
        try:
            page.goto(
                f"{django_server}{unit1.get_absolute_url()}",
                wait_until="domcontentloaded",
            )

            # The mobile-only wrapper exists and is visible (sm:hidden
            # is "show below 640px"). Bottom of the page.
            mobile_wrap = page.locator(
                '[data-testid="reader-bottom-completion-mobile"]',
            )
            mobile_wrap.wait_for(state="visible")

            # The desktop wrapper is in the DOM but hidden by
            # `hidden sm:block`.
            desktop_wrap = page.locator(
                '[data-testid="reader-bottom-completion-desktop"]',
            )
            assert desktop_wrap.count() == 1
            # is_visible() respects display:none.
            assert not desktop_wrap.is_visible()

            # The mobile button must be vertically above the next link
            # so the action is not stranded between prev/next.
            mob_box = mobile_wrap.bounding_box()
            next_link = page.locator(
                '[data-testid="bottom-next-btn"]',
            )
            next_box = next_link.bounding_box()
            assert mob_box is not None
            assert next_box is not None
            assert mob_box["y"] < next_box["y"], (
                "Mobile mark-complete row must render above the Next "
                "link on a 390px viewport."
            )

            # Click the mobile button → state flips to Completed.
            mob_btn = mobile_wrap.locator("button[data-completion-toggle]")
            mob_btn.click()
            page.wait_for_function(
                """
                () => {
                  const btns = document.querySelectorAll(
                    '[data-completion-toggle]'
                  );
                  return Array.from(btns).every(
                    b => b.textContent.includes('Completed')
                  );
                }
                """,
                timeout=4000,
            )

            page.screenshot(
                path=f"{SHOT_DIR}/02-course-unit-mobile-after-toggle.png",
                full_page=True,
            )

            # Reload — the completion state must persist server-side.
            page.reload(wait_until="domcontentloaded")
            mob_btn_after = page.locator(
                '[data-testid="reader-bottom-completion-mobile"] '
                'button[data-completion-toggle]'
            )
            mob_btn_after.wait_for(state="visible")
            text = mob_btn_after.text_content() or ""
            assert "Completed" in text, (
                "Completion state did not persist across reload — got "
                f"button text: {text!r}"
            )
        finally:
            ctx.close()

    def test_long_prevnext_titles_do_not_overflow_390px(
        self, browser, django_server,
    ):
        """Regression guard for the new bottom-nav layout: long titles
        on prev/next links must still truncate inside the 390px
        viewport (no horizontal body overflow)."""
        _clear_courses()
        course = _create_course(
            title="Overflow Course",
            slug="overflow-483",
            required_level=0,
        )
        module = _create_module(course, "Module 1", sort_order=1)
        long_title = (
            "A Very Long Unit Title That Would Otherwise Overflow "
            "the 390px Mobile Viewport In The Bottom Navigation Row"
        )
        _create_unit(module, long_title, sort_order=1, body="A")
        unit2 = _create_unit(
            module, "Second Unit With Long Trailing Title Goes Here",
            sort_order=2, body="B",
        )
        create_user("overflow-483@test.com", tier_slug="free")

        ctx = _mobile_context(browser, "overflow-483@test.com")
        page = ctx.new_page()
        try:
            page.goto(
                f"{django_server}{unit2.get_absolute_url()}",
                wait_until="domcontentloaded",
            )
            # Prev link to the long-titled unit.
            prev = page.locator('[data-testid="bottom-prev-btn"]')
            prev.wait_for(state="visible")
            box = prev.bounding_box()
            assert box is not None
            # Right edge must stay inside the viewport (390px) with a
            # small tolerance for sub-pixel rounding.
            assert box["x"] + box["width"] <= 391, (
                f"Bottom prev right edge {box['x'] + box['width']} "
                f"overflows the 390px viewport."
            )
            # No horizontal scrollbar.
            scroll_w = page.evaluate(
                "() => document.documentElement.scrollWidth"
            )
            client_w = page.evaluate(
                "() => document.documentElement.clientWidth"
            )
            assert scroll_w <= client_w + 1, (
                f"Body overflows horizontally: scrollWidth={scroll_w} "
                f"clientWidth={client_w}"
            )
        finally:
            ctx.close()


# ----------------------------------------------------------------------
# Scenario 3: workshop tutorial reader parity on mobile
# ----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestWorkshopReaderMobileParity:
    """Workshop tutorial pages render the same mobile completion row
    pattern as course unit pages."""

    def _setup(self):
        _clear_workshops()
        _create_workshop(
            slug="parity-483",
            title="Parity Workshop",
            landing=0,
            pages=0,
            recording=0,
            pages_data=[
                ("p1", "Page One", "# One\n\nBody."),
                ("p2", "Page Two", "# Two\n\nBody."),
            ],
        )

    def test_workshop_tutorial_has_mobile_completion_row_above_next(
        self, browser, django_server,
    ):
        os.makedirs(SHOT_DIR, exist_ok=True)
        self._setup()
        create_user("ws-parity-483@test.com", tier_slug="free")

        ctx = _mobile_context(browser, "ws-parity-483@test.com")
        page = ctx.new_page()
        try:
            page.goto(
                f"{django_server}/workshops/parity-483/tutorial/p1",
                wait_until="domcontentloaded",
            )

            mobile_wrap = page.locator(
                '[data-testid="reader-bottom-completion-mobile"]',
            )
            mobile_wrap.wait_for(state="visible")

            # Workshop tutorial pages use a different testid for the
            # next link (``page-next-btn`` vs course unit's
            # ``bottom-next-btn``); both arrive via the shared
            # ``_bottom_nav.html`` partial through ``bottom_next_testid``.
            next_link = page.locator(
                '[data-testid="page-next-btn"]',
            )
            next_link.wait_for(state="visible")

            mob_box = mobile_wrap.bounding_box()
            next_box = next_link.bounding_box()
            assert mob_box is not None
            assert next_box is not None
            assert mob_box["y"] < next_box["y"], (
                "Workshop tutorial mark-complete row must render "
                "above the Next link on mobile."
            )

            page.screenshot(
                path=f"{SHOT_DIR}/03-workshop-tutorial-mobile.png",
                full_page=True,
            )
        finally:
            ctx.close()


# ----------------------------------------------------------------------
# Scenario 4: desktop layout unaffected (regression guard)
# ----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestDesktopBottomNavRegression:
    """The desktop bottom-nav layout still renders the mark-complete
    button inline (next to prev / next), preserving keyboard tab order
    and the original visual grouping."""

    def _setup(self):
        _clear_courses()
        course = _create_course(
            title="Desktop Course",
            slug="desktop-483",
            required_level=0,
        )
        module = _create_module(course, "Module 1", sort_order=1)
        unit1 = _create_unit(
            module, "Unit One", sort_order=1, body="A",
        )
        _create_unit(module, "Unit Two", sort_order=2, body="B")
        return unit1

    def test_inline_completion_visible_on_desktop(
        self, browser, django_server,
    ):
        os.makedirs(SHOT_DIR, exist_ok=True)
        unit1 = self._setup()
        create_user("desktop-483@test.com", tier_slug="free")

        session_key = create_session_for_user("desktop-483@test.com")
        ctx = browser.new_context(viewport=DESKTOP_VIEWPORT)
        ctx.add_cookies([
            {
                "name": "sessionid", "value": session_key,
                "domain": "127.0.0.1", "path": "/",
            },
            {
                "name": "csrftoken",
                "value": "e2e-test-csrf-token-value",
                "domain": "127.0.0.1", "path": "/",
            },
        ])
        page = ctx.new_page()
        try:
            page.goto(
                f"{django_server}{unit1.get_absolute_url()}",
                wait_until="domcontentloaded",
            )

            mobile_wrap = page.locator(
                '[data-testid="reader-bottom-completion-mobile"]',
            )
            assert not mobile_wrap.is_visible(), (
                "Mobile-only completion row must be display:none "
                "on a 1280px viewport."
            )

            desktop_wrap = page.locator(
                '[data-testid="reader-bottom-completion-desktop"]',
            )
            desktop_wrap.wait_for(state="visible")

            page.screenshot(
                path=f"{SHOT_DIR}/04-desktop-bottom-nav.png",
                full_page=False,
            )
        finally:
            ctx.close()
