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
# Scenario 1: Returning visitor expands transcript and chapters and sees a
# single visual language (1280x900 desktop screenshot).
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_workshop_video_desktop_summaries_share_typography(
    django_server, browser
):
    _clear_workshops()
    _create_workshop_with_full_video()
    ensure_site_config_tiers()
    _create_user('main@test.com', tier_slug='main')

    ctx = _auth_context(browser, 'main@test.com')
    page = ctx.new_page()
    page.set_viewport_size(DESKTOP)
    page.goto(
        f'{django_server}/workshops/test-ws/video',
        wait_until='domcontentloaded',
    )

    # Both accordions exist.
    chapters = page.locator('details[data-testid="video-chapters"]')
    transcript = page.locator('details[data-testid="video-transcript"]')
    assert chapters.count() == 1, 'Chapters accordion not rendered'
    assert transcript.count() == 1, 'Transcript accordion not rendered'

    # Read computed styles BEFORE any interaction so the hover/focus pseudo
    # classes don't change `color: hover:text-accent`. The assertion is on
    # the resting summary typography, not on the hovered state.
    chapters_style = _summary_style(
        page, 'details[data-testid="video-chapters"] > summary'
    )
    transcript_style = _summary_style(
        page, 'details[data-testid="video-transcript"] > summary'
    )

    # Both summaries are text-base (16px) sentence-case foreground.
    for label, style in [('chapters', chapters_style),
                         ('transcript', transcript_style)]:
        assert style is not None, f'{label} summary not found'
        assert style['fontSize'] == '16px', (
            f'{label} summary fontSize is {style["fontSize"]}, '
            f'expected 16px (text-base)'
        )
        assert style['fontWeight'] == EXPECTED_SUMMARY_FONT_WEIGHT, (
            f'{label} summary fontWeight is {style["fontWeight"]}, '
            f'expected 500 (font-medium)'
        )
        assert style['textTransform'] == 'none', (
            f'{label} summary textTransform is {style["textTransform"]}, '
            f'expected none (no uppercase)'
        )

    # And they share font-size + font-weight + color (this is the
    # "look like siblings" assertion).
    assert chapters_style['fontSize'] == transcript_style['fontSize']
    assert chapters_style['fontWeight'] == transcript_style['fontWeight']
    assert chapters_style['color'] == transcript_style['color']

    # Now expand both for the screenshot, and move the cursor away so the
    # captured image shows the resting (non-hover) state.
    chapters.locator('summary').click()
    transcript.locator('summary').click()
    page.mouse.move(0, 0)
    page.wait_for_timeout(150)

    # Save the screenshot for the [HUMAN] visual review.
    _save_screenshot(page, 'workshop-video-desktop')

    ctx.close()


# ---------------------------------------------------------------------------
# Scenario 2: Mobile workshop visitor sees consistent accordion typography
# (393x851 mobile screenshot).
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_workshop_video_mobile_summaries_share_typography(
    django_server, browser
):
    _clear_workshops()
    _create_workshop_with_full_video()
    ensure_site_config_tiers()
    _create_user('main@test.com', tier_slug='main')

    ctx = _auth_context(browser, 'main@test.com')
    page = ctx.new_page()
    page.set_viewport_size(MOBILE)
    page.goto(
        f'{django_server}/workshops/test-ws/video',
        wait_until='domcontentloaded',
    )

    chapters = page.locator('details[data-testid="video-chapters"]')
    transcript = page.locator('details[data-testid="video-transcript"]')
    assert chapters.count() == 1
    assert transcript.count() == 1

    # Read computed styles BEFORE clicking so hover/focus state doesn't
    # leak into the assertions.
    chapters_style = _summary_style(
        page, 'details[data-testid="video-chapters"] > summary'
    )
    transcript_style = _summary_style(
        page, 'details[data-testid="video-transcript"] > summary'
    )

    for label, style in [('chapters', chapters_style),
                         ('transcript', transcript_style)]:
        assert style is not None
        assert style['fontSize'] == '16px', (
            f'{label} mobile summary fontSize is {style["fontSize"]}'
        )
        assert style['fontWeight'] == EXPECTED_SUMMARY_FONT_WEIGHT
        assert style['textTransform'] == 'none'

    # Expand both for the screenshot, then drop the cursor off-canvas so
    # the captured image shows the resting state.
    chapters.locator('summary').click()
    transcript.scroll_into_view_if_needed()
    transcript.locator('summary').click()
    page.mouse.move(0, 0)
    page.wait_for_timeout(150)

    _save_screenshot(page, 'workshop-video-mobile')

    ctx.close()


# ---------------------------------------------------------------------------
# Scenario 3: FAQ ↔ workshop accordion typography rhymes (desktop).
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_faq_and_workshop_chapters_share_color_and_weight(
    django_server, browser
):
    _clear_workshops()
    _create_workshop_with_full_video()
    ensure_site_config_tiers()
    # Recording is gated to Main tier; auth as Main so the chapters
    # accordion actually renders below the video player.
    _create_user('main@test.com', tier_slug='main')

    # Anonymous context for the FAQ read (FAQ is public on the homepage).
    anon_ctx = browser.new_context(viewport=DESKTOP)
    anon_page = anon_ctx.new_page()
    anon_page.goto(f'{django_server}/', wait_until='domcontentloaded')
    anon_page.locator('#faq').scroll_into_view_if_needed()
    anon_page.wait_for_timeout(150)
    faq_style = anon_page.evaluate(
        """() => {
            const btn = document.querySelector(
                '#faq .faq-item button'
            );
            if (!btn) return null;
            const cs = getComputedStyle(btn);
            return {
                fontWeight: cs.fontWeight,
                color: cs.color,
                textTransform: cs.textTransform,
                fontFamily: cs.fontFamily,
            };
        }"""
    )
    assert faq_style is not None, 'FAQ summary not found'
    anon_ctx.close()

    # Auth context for the workshop video page (recording-gated).
    ctx = _auth_context(browser, 'main@test.com')
    page = ctx.new_page()
    page.set_viewport_size(DESKTOP)
    page.goto(
        f'{django_server}/workshops/test-ws/video',
        wait_until='domcontentloaded',
    )
    chapters_style = _summary_style(
        page, 'details[data-testid="video-chapters"] > summary'
    )
    assert chapters_style is not None, (
        'Chapters accordion missing — Main tier should bypass the '
        'recording paywall'
    )

    # FAQ button uses default <button> font-weight (400) plus the global
    # foreground color, while the accordion summary explicitly sets
    # font-medium (500). The cross-check is that both share the same
    # foreground COLOR, sentence case, and the Inter font family — the
    # accordion is a section header (a step heavier) but still the same
    # design system, not muted-uppercase.
    assert chapters_style['color'] == faq_style['color'], (
        f'Chapters color {chapters_style["color"]} differs from '
        f'FAQ color {faq_style["color"]}'
    )
    assert chapters_style['textTransform'] == 'none'
    assert faq_style['textTransform'] == 'none'
    # Both come from the Inter font family configured in tailwind.config.
    assert 'Inter' in chapters_style['fontFamily']
    assert 'Inter' in faq_style['fontFamily']

    ctx.close()


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


# ---------------------------------------------------------------------------
# Scenario 6: No regression — accordions still open and close, and the
# chevron icon rotates 180° when [open].
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_accordions_still_open_close_with_chevron_rotation(
    django_server, browser
):
    _clear_workshops()
    _create_workshop_with_full_video()
    ensure_site_config_tiers()
    _create_user('main@test.com', tier_slug='main')

    ctx = _auth_context(browser, 'main@test.com')
    page = ctx.new_page()
    page.set_viewport_size(DESKTOP)

    errors = []
    page.on('pageerror', lambda exc: errors.append(str(exc)))

    page.goto(
        f'{django_server}/workshops/test-ws/video',
        wait_until='domcontentloaded',
    )

    for testid in ('video-transcript', 'video-chapters'):
        details = page.locator(f'details[data-testid="{testid}"]')
        assert details.count() == 1
        summary = details.locator('summary')
        chevron = details.locator('.accordion-chevron')

        # Initially closed -> chevron rotation is 0° (the CSS rule only
        # sets transform when [open]).
        rotation_closed = chevron.first.evaluate(
            "el => getComputedStyle(el).transform"
        )
        assert rotation_closed in ('none', 'matrix(1, 0, 0, 1, 0, 0)'), (
            f'{testid} chevron rotation when closed is {rotation_closed!r}, '
            f'expected `none` (no transform applied)'
        )

        # Click to open.
        summary.click()
        page.wait_for_function(
            "el => el.hasAttribute('open')",
            arg=details.first.element_handle(),
        )
        # The transform animates over 200ms (`transition-transform
        # duration-200`); wait for it to finish before reading the
        # computed matrix.
        page.wait_for_timeout(350)
        # After [open], the CSS rotates the chevron 180°. The matrix is
        # (-1, 0, 0, -1, 0, 0) for a 180° rotation; allow small float
        # drift on the off-diagonal entries.
        rotation_open = chevron.first.evaluate(
            "el => getComputedStyle(el).transform"
        )
        assert rotation_open != rotation_closed, (
            f'{testid} chevron transform did not change when opened '
            f'(closed={rotation_closed!r}, open={rotation_open!r})'
        )
        # Confirm the rotation is ~180° by parsing the matrix and
        # checking the (a, d) diagonal is approximately (-1, -1).
        diag = page.evaluate(
            """sel => {
                const el = document.querySelector(sel);
                const m = getComputedStyle(el).transform;
                const match = m.match(/matrix\\(([^)]+)\\)/);
                if (!match) return null;
                const parts = match[1].split(',').map(s => parseFloat(s));
                return { a: parts[0], d: parts[3] };
            }""",
            f'details[data-testid="{testid}"] .accordion-chevron'
        )
        assert diag is not None, (
            f'{testid} chevron transform did not produce a matrix; '
            f'got {rotation_open!r}'
        )
        assert diag['a'] < -0.95 and diag['d'] < -0.95, (
            f'{testid} chevron rotation is not ~180°: matrix diag '
            f'(a, d) = ({diag["a"]}, {diag["d"]})'
        )

        # Click to close again.
        summary.click()
        page.wait_for_function(
            "el => !el.hasAttribute('open')",
            arg=details.first.element_handle(),
        )
        page.wait_for_timeout(350)
        rotation_closed_again = chevron.first.evaluate(
            "el => getComputedStyle(el).transform"
        )
        assert rotation_closed_again in (
            'none', 'matrix(1, 0, 0, 1, 0, 0)'
        )

    assert errors == [], (
        f'Console errors fired during open/close: {errors}'
    )

    ctx.close()


# ---------------------------------------------------------------------------
# Scenario 7: Tier card heading vs. accordion summary — same design system.
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_tier_card_and_accordion_share_design_system(django_server, browser):
    _clear_workshops()
    _create_workshop_with_full_video()
    ensure_site_config_tiers()
    # The workshop's recording is gated to Main tier (level 20). Use a
    # Main user so the chapters accordion actually renders below the
    # video, otherwise the page renders the paywall card instead.
    _create_user('main@test.com', tier_slug='main')

    # Anonymous context for the homepage tier card read.
    anon_ctx = browser.new_context(viewport=DESKTOP)
    anon_page = anon_ctx.new_page()
    anon_page.goto(f'{django_server}/', wait_until='domcontentloaded')
    anon_page.locator('#tiers').scroll_into_view_if_needed()
    anon_page.wait_for_timeout(150)

    # Read the first tier-card <h3> computed style. Pricing card titles
    # render as headings in the homepage tier section.
    tier_h3_style = anon_page.evaluate(
        """() => {
            const h3 = document.querySelector('#tiers h3');
            if (!h3) return null;
            const cs = getComputedStyle(h3);
            return {
                fontSize: cs.fontSize,
                fontWeight: cs.fontWeight,
                fontFamily: cs.fontFamily,
                textTransform: cs.textTransform,
            };
        }"""
    )
    assert tier_h3_style is not None, 'No tier card <h3> found on #tiers'
    anon_ctx.close()

    # Auth context to read the chapters summary on the recording page.
    ctx = _auth_context(browser, 'main@test.com')
    page = ctx.new_page()
    page.set_viewport_size(DESKTOP)
    page.goto(
        f'{django_server}/workshops/test-ws/video',
        wait_until='domcontentloaded',
    )
    chapters_style = _summary_style(
        page, 'details[data-testid="video-chapters"] > summary'
    )
    assert chapters_style is not None

    # The tier h3 is heavier (font-semibold = 600). The accordion is one
    # step lighter (font-medium = 500). Both share the Inter family,
    # sentence case, and come from the same design system (the failure
    # mode this test guards is reverting the accordion to
    # uppercase/muted, which would no longer rhyme).
    assert 'Inter' in tier_h3_style['fontFamily']
    assert 'Inter' in chapters_style['fontFamily']
    assert tier_h3_style['textTransform'] == 'none'
    assert chapters_style['textTransform'] == 'none'
    # Accordion summary is text-base (16px). Tier h3 is text-lg (18px) —
    # the accordion is one step smaller, mirroring the issue's "section
    # header that is one step smaller than the tier-card heading".
    assert chapters_style['fontSize'] == '16px'
    # Tier h3 size is at least the same or larger than the accordion.
    tier_size_px = int(float(tier_h3_style['fontSize'].rstrip('px')))
    chapters_size_px = int(float(chapters_style['fontSize'].rstrip('px')))
    assert tier_size_px >= chapters_size_px, (
        f'Tier h3 size {tier_size_px}px should be >= accordion summary '
        f'{chapters_size_px}px'
    )

    ctx.close()
