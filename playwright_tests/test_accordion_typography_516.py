"""Playwright E2E tests for issue #516 — accordion typography is consistent
across the workshop video page, course syllabus, reader sidebar, and the
homepage FAQ.

Before this issue, four accordion-or-accordion-like patterns rendered with
four different summary styles (text-sm vs text-base, uppercase vs sentence
case, muted vs foreground). The fix:

- Extracts a shared `templates/includes/_accordion.html` partial and uses
  it for the workshop transcript ("Show transcript") and the video
  Chapters section, both of which now render their summary in
  `text-base font-medium text-foreground`.
- Retunes the reader sidebar's course-module summary from
  `text-xs font-semibold uppercase tracking-wider text-muted-foreground`
  to `text-sm font-medium text-foreground` to match the main syllabus
  module summary in case + weight + color.

These tests assert the computed CSS of each summary element so a future
regression to the old uppercase/text-xs styling fails loudly. They also
take screenshots at desktop 1280x900 and mobile 393x851 viewports for
the issue's `[HUMAN]` visual review.

Screenshots are written to ``playwright_tests/screenshots/issue-516/``.

Usage:
    uv run pytest playwright_tests/test_accordion_typography_516.py -v
"""

import datetime
import os
from pathlib import Path

import pytest

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_user as _create_user,
)
from playwright_tests.conftest import (
    ensure_site_config_tiers,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402

SCREENSHOT_DIR = Path("playwright_tests/screenshots/issue-516")
DESKTOP = {"width": 1280, "height": 900}
MOBILE = {"width": 393, "height": 851}

# Computed CSS we expect for an accordion section-header summary.
EXPECTED_SUMMARY_FONT_SIZE_PX = 16  # text-base
EXPECTED_SUMMARY_FONT_WEIGHT = "500"  # font-medium


def _save_screenshot(page, name):
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SCREENSHOT_DIR / f"{name}.png", full_page=True)


def _summary_style(page, locator_selector):
    """Return computed { fontSize, fontWeight, color, textTransform } for a
    summary element identified by the given Playwright selector."""
    return page.evaluate(
        """selector => {
            const el = document.querySelector(selector);
            if (!el) return null;
            const cs = getComputedStyle(el);
            return {
                fontSize: cs.fontSize,
                fontWeight: cs.fontWeight,
                color: cs.color,
                textTransform: cs.textTransform,
                fontFamily: cs.fontFamily,
                letterSpacing: cs.letterSpacing,
            };
        }""",
        locator_selector,
    )


def _clear_workshops():
    from content.models import Workshop, WorkshopPage
    from events.models import Event
    WorkshopPage.objects.all().delete()
    Workshop.objects.all().delete()
    Event.objects.all().delete()
    connection.close()


def _create_workshop_with_full_video():
    """Workshop with a YouTube recording, chapters, and a transcript so
    the video page renders all three accordion-or-accordion-style sections.
    """
    from django.utils import timezone
    from django.utils.text import slugify

    from content.models import (
        Instructor,
        Workshop,
        WorkshopInstructor,
        WorkshopPage,
    )
    from events.models import Event

    event = Event.objects.create(
        slug='test-ws-event',
        title='Test workshop',
        start_datetime=timezone.now(),
        status='completed',
        kind='workshop',
        recording_url='https://www.youtube.com/watch?v=dQw4w9WgXcQ',
        timestamps=[
            {'time': '0:00', 'title': 'Welcome'},
            {'time': '5:00', 'title': 'Setup'},
            {'time': '12:30', 'title': 'Deploy'},
        ],
        materials=[],
        transcript_text=(
            'Hello and welcome to the workshop.\n'
            'In this session we will build a small system end to end.\n'
            'Thanks for joining and let us get started.'
        ),
        published=True,
    )
    workshop = Workshop.objects.create(
        slug='test-ws',
        title='Workshop Typography Test',
        date=datetime.date(2026, 4, 21),
        status='published',
        landing_required_level=0,
        pages_required_level=10,
        recording_required_level=20,
        description='Workshop body.',
        event=event,
    )
    instructor_name = 'Alexey'
    instructor, _ = Instructor.objects.get_or_create(
        instructor_id=slugify(instructor_name)[:200] or 'test-instructor',
        defaults={'name': instructor_name, 'status': 'published'},
    )
    WorkshopInstructor.objects.get_or_create(
        workshop=workshop,
        instructor=instructor,
        defaults={'position': 0},
    )
    WorkshopPage.objects.create(
        workshop=workshop, slug='intro', title='Intro',
        sort_order=1, body='# Welcome',
    )
    connection.close()
    return workshop


def _clear_courses():
    from content.models import Course
    Course.objects.all().delete()
    connection.close()


def _create_course_with_two_units():
    """Course with one published module and two units."""
    from django.utils.text import slugify

    from content.models import Course, Module, Unit

    course = Course.objects.create(
        title='Test Course',
        slug='test-course',
        description='A test course.',
        required_level=0,
        status='published',
    )
    module = Module.objects.create(
        course=course,
        title='Module One',
        slug=slugify('Module One'),
        sort_order=0,
    )
    Unit.objects.create(
        module=module,
        title='Unit One',
        slug=slugify('Unit One'),
        sort_order=0,
        body='# Unit One',
    )
    Unit.objects.create(
        module=module,
        title='Unit Two',
        slug=slugify('Unit Two'),
        sort_order=1,
        body='# Unit Two',
    )
    connection.close()
    return course


# ---------------------------------------------------------------------------
# Issue #618: scenarios 1, 2, 3 (workshop video Chapters accordion
# typography on desktop, mobile, and FAQ comparison) were retired with
# the standalone /workshops/<slug>/video page. The new course-player
# layout renders chapter rows as plain <button>/<div> elements inside
# `_workshop_outline.html`, not as a <details>/<summary> accordion.
# The remaining scenarios still cover:
# - Course syllabus + reader sidebar module typography (scenario 4).
# - Reader sidebar mobile module typography (scenario 5).
# Transcript accordion typography is implicitly covered by
# `_recording_transcript.html` which still uses the shared section-header
# partial on the events surface.

# ---------------------------------------------------------------------------
# Scenario 4: Course syllabus and reader sidebar agree on module typography.
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_syllabus_and_reader_sidebar_share_module_typography(
    django_server, browser
):
    _clear_courses()
    _create_course_with_two_units()
    ensure_site_config_tiers()
    _create_user('main@test.com', tier_slug='main')

    ctx = _auth_context(browser, 'main@test.com')
    page = ctx.new_page()
    page.set_viewport_size(DESKTOP)

    # Step 1: read the syllabus module summary on the course detail page.
    page.goto(
        f'{django_server}/courses/test-course/',
        wait_until='domcontentloaded',
    )
    syllabus_style = _summary_style(
        page, '[data-testid="syllabus-module-summary"]'
    )
    assert syllabus_style is not None, 'Syllabus module summary not found'
    assert syllabus_style['fontSize'] == '16px', (
        f'Syllabus summary fontSize is {syllabus_style["fontSize"]}, '
        f'expected 16px (text-base)'
    )
    assert syllabus_style['fontWeight'] == EXPECTED_SUMMARY_FONT_WEIGHT
    assert syllabus_style['textTransform'] == 'none'

    _save_screenshot(page, 'syllabus-desktop')

    # Step 2: read the sidebar module label on a unit page. The label is
    # the `<span>` immediately inside `details.sidebar-module > summary`.
    # We read computed style of that span (the visible text), not of the
    # summary itself, since the summary's text-color/text-transform is
    # inherited but the span carries the explicit Tailwind classes.
    page.goto(
        f'{django_server}/courses/test-course/module-one/unit-one',
        wait_until='domcontentloaded',
    )
    sidebar_style = page.evaluate(
        """() => {
            const span = document.querySelector(
                '#sidebar-nav details.sidebar-module > summary > span'
            );
            if (!span) return null;
            const cs = getComputedStyle(span);
            return {
                fontSize: cs.fontSize,
                fontWeight: cs.fontWeight,
                color: cs.color,
                textTransform: cs.textTransform,
            };
        }"""
    )
    assert sidebar_style is not None, 'Reader sidebar module label not found'

    # The sidebar uses text-sm (14px) because the column is narrower, but
    # weight + case + color must match the main syllabus.
    assert sidebar_style['fontSize'] == '14px', (
        f'Sidebar module label fontSize is {sidebar_style["fontSize"]}, '
        f'expected 14px (text-sm). The old text-xs (12px) shorthand was '
        f'the regression that #516 fixes.'
    )
    assert sidebar_style['fontWeight'] == EXPECTED_SUMMARY_FONT_WEIGHT, (
        f'Sidebar module fontWeight is {sidebar_style["fontWeight"]}, '
        f'expected 500 (font-medium). The old font-semibold (600) was '
        f'the regression that #516 fixes.'
    )
    assert sidebar_style['textTransform'] == 'none', (
        f'Sidebar module textTransform is '
        f'{sidebar_style["textTransform"]}, expected none. The old '
        f'uppercase styling was the regression that #516 fixes.'
    )
    # Sidebar and syllabus share the foreground color (font-color HSL
    # resolves to the same `rgb(...)` regardless of theme).
    assert sidebar_style['color'] == syllabus_style['color'], (
        f'Sidebar color {sidebar_style["color"]} differs from syllabus '
        f'color {syllabus_style["color"]}'
    )

    _save_screenshot(page, 'reader-sidebar-desktop')

    ctx.close()


# ---------------------------------------------------------------------------
# Scenario 5: Mobile reader sidebar accordion matches the syllabus accordion.
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_reader_sidebar_mobile_module_typography(django_server, browser):
    _clear_courses()
    _create_course_with_two_units()
    ensure_site_config_tiers()
    _create_user('main@test.com', tier_slug='main')

    ctx = _auth_context(browser, 'main@test.com')
    page = ctx.new_page()
    page.set_viewport_size(MOBILE)

    page.goto(
        f'{django_server}/courses/test-course/module-one/unit-one',
        wait_until='domcontentloaded',
    )

    # On mobile the sidebar nav is hidden behind a `Course Navigation`
    # toggle. Use click() rather than tap() because the auth_context
    # browser context isn't created with `hasTouch=True`.
    toggle = page.locator('#sidebar-toggle-btn')
    if toggle.count() == 1 and toggle.is_visible():
        toggle.click()
        page.wait_for_timeout(150)

    # Wait for the nav to be visible.
    page.locator('#sidebar-nav').wait_for(state='visible', timeout=2000)
    page.wait_for_timeout(100)

    sidebar_style = page.evaluate(
        """() => {
            const span = document.querySelector(
                '#sidebar-nav details.sidebar-module > summary > span'
            );
            if (!span) return null;
            const cs = getComputedStyle(span);
            return {
                fontSize: cs.fontSize,
                fontWeight: cs.fontWeight,
                textTransform: cs.textTransform,
            };
        }"""
    )
    assert sidebar_style is not None, (
        'Reader sidebar module label not found on mobile'
    )
    assert sidebar_style['fontSize'] == '14px'
    assert sidebar_style['fontWeight'] == EXPECTED_SUMMARY_FONT_WEIGHT
    assert sidebar_style['textTransform'] == 'none'

    _save_screenshot(page, 'reader-sidebar-mobile')

    ctx.close()


# Issue #618: scenarios 6 (chevron rotation on workshop chapters /
# transcript accordions) and 7 (tier-card + accordion design parity)
# both depended on the legacy /workshops/<slug>/video page hosting the
# Chapters accordion. With that page retired the tests are obsolete:
# - Chevron rotation is still exercised by the homepage FAQ accordion
#   (covered by the FAQ tests in the design-system page).
# - Tier-card / accordion typography parity is enforced by the design
#   system's shared utility classes; the section-header accordion
#   partial is unchanged and used by the events transcript accordion.
