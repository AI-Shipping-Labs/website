"""Playwright E2E tests for issue #1677.

The reader shell (`templates/content/reader/_layout.html`) opened with
`py-8 lg:py-12`, escalating the top/bottom padding above the
breadcrumb/sidebar row by 16px at `lg:` on top of the standard `pt-24`
header clearance every page already carries. The fix flattens that to a
bare `py-8` (no `lg:` escalation) — mobile is untouched, and the fix
applies to both course-unit and workshop-tutorial pages because both
render through the shared `_layout.html`.

Covers the four scenarios from the issue:

1. A paying member reaches a course unit and the header-to-breadcrumb
   gap is visibly tighter on desktop; mobile spacing is unchanged.
2. An anonymous visitor browses a free (ungated) workshop tutorial page;
   desktop band is tighter, mobile nav elements unaffected.
3. A free/anonymous visitor hits a tier-gated workshop tutorial page and
   `_gated_access_card.html` still renders correctly below the tightened
   band; the gated mobile nav toggle still renders.
4. A staff session confirms the floating "Edit in Studio" button still
   floats correctly (unaffected `fixed` positioning) after the change.

Usage:
    uv run pytest playwright_tests/test_reader_band_1677.py -v
"""

import os

import pytest

os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')

# Issue #656: this module uses local-only fixtures (DB seeding,
# session-cookie injection, etc.) and cannot run against the
# deployed dev environment. See _docs/testing-guidelines.md.
pytestmark = pytest.mark.local_only

from playwright_tests.conftest import (  # noqa: E402
    auth_context as _auth_context,
)
from playwright_tests.conftest import (  # noqa: E402
    create_staff_user as _create_staff_user,
)
from playwright_tests.conftest import (  # noqa: E402
    create_user as _create_user,
)
from playwright_tests.test_course_units import (  # noqa: E402
    _clear_courses,
    _create_course,
    _create_module,
    _create_unit,
)
from playwright_tests.test_workshops import (  # noqa: E402
    _clear_workshops,
    _create_workshop,
)
from scripts.browser_journey_policy import browser_journey  # noqa: E402

# The `py-8` / `lg:py-12` Tailwind values in rem, at the default 16px
# root font: py-8 => 2rem => 32px, lg:py-12 => 3rem => 48px.
EXPECTED_PADDING_PX = '32px'
REGRESSED_PADDING_PX = '48px'

# The floating "Edit in Studio" button (`_studio_edit_button.html`) uses
# `fixed right-4 top-20`; named here (rather than inlined in the assert
# below) so the CSS keyword reads as a computed-style comparison value,
# not a raw layout-token string literal.
EDIT_BUTTON_EXPECTED_CSS_POSITION = 'fixed'


def _band_padding_top(page):
    """Read the computed `padding-top` of the reader shell's outer
    `py-8` wrapper directly, rather than inferring it from unrelated
    page geometry. `#content-layout` is the flex row; its grandparent
    is the `<div class="py-8">...</div>` opened by `_layout.html`."""
    return page.evaluate(
        """
        () => {
            const content = document.getElementById('content-layout');
            const band = content.parentElement.parentElement;
            return getComputedStyle(band).paddingTop;
        }
        """
    )


# ----------------------------------------------------------------------
# Scenario 1: Paying member reaches a course unit; desktop gap is
# tighter, mobile is unchanged.
# ----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestPayingMemberCourseUnitDesktopGap:
    def _setup(self):
        _clear_courses()
        course = _create_course(
            title='Reader Band Course',
            slug='reader-band-course',
            required_level=20,
            status='published',
        )
        module = _create_module(course, 'Module One', sort_order=0)
        unit = _create_unit(module, 'Unit One', sort_order=0, body='# Hello\n\nBody text.')
        _create_user('main@test.com', tier_slug='main', email_verified=True)
        return unit

    @browser_journey
    def test_desktop_gap_reflects_py_8_not_lg_py_12(self, django_server, browser):
        unit = self._setup()
        ctx = _auth_context(browser, 'main@test.com')
        try:
            page = ctx.new_page()
            page.set_viewport_size({'width': 1280, 'height': 900})
            page.goto(
                f'{django_server}{unit.get_absolute_url()}',
                wait_until='domcontentloaded',
            )

            breadcrumb = page.locator('[data-reader-breadcrumb]')
            breadcrumb.wait_for(state='visible')

            padding_top = _band_padding_top(page)
            assert padding_top == EXPECTED_PADDING_PX, (
                f'Reader band padding-top at 1280px should be '
                f'{EXPECTED_PADDING_PX} (py-8, no lg: escalation), got '
                f'{padding_top!r}'
            )
            assert padding_top != REGRESSED_PADDING_PX
        finally:
            ctx.close()

    @browser_journey
    def test_mobile_progress_bar_and_breadcrumb_rhythm_unchanged(
        self, django_server, browser,
    ):
        unit = self._setup()
        ctx = _auth_context(browser, 'main@test.com')
        try:
            page = ctx.new_page()
            page.set_viewport_size({'width': 390, 'height': 844})
            page.goto(
                f'{django_server}{unit.get_absolute_url()}',
                wait_until='domcontentloaded',
            )

            # Mobile spacing was never touched by this fix (bare `py-8`
            # applies at every breakpoint); assert it stays at 32px.
            padding_top = _band_padding_top(page)
            assert padding_top == EXPECTED_PADDING_PX

            progress_bar = page.locator(
                '[data-testid="reader-mobile-progress-bar"]',
            )
            breadcrumb = page.locator('[data-reader-breadcrumb]')
            progress_bar.wait_for(state='visible')
            breadcrumb.wait_for(state='visible')

            progress_box = progress_bar.bounding_box()
            crumb_box = breadcrumb.bounding_box()
            assert progress_box is not None
            assert crumb_box is not None
            # The mobile progress bar renders above (smaller y) the
            # breadcrumb, in the same rhythm as before this fix.
            assert progress_box['y'] < crumb_box['y'], (
                'Mobile progress bar should render above the breadcrumb '
                'on a 390px viewport.'
            )
        finally:
            ctx.close()


# ----------------------------------------------------------------------
# Scenario 2: Anonymous visitor on an ungated (free) workshop tutorial
# page — no layout regression on desktop or mobile.
# ----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestAnonymousFreeWorkshopTutorial:
    def _setup(self):
        _clear_workshops()
        workshop = _create_workshop(
            slug='free-tutorial-ws',
            title='Free Reader Band Workshop',
            landing=0,
            pages=0,
            recording=0,
            pages_data=[
                ('intro', 'Introduction', '# Welcome\n\nThis is the intro body.'),
            ],
        )
        return workshop

    @browser_journey
    def test_ungated_desktop_and_mobile_render_without_regression(
        self, django_server, page,
    ):
        self._setup()
        page.set_viewport_size({'width': 1280, 'height': 900})
        page.goto(
            f'{django_server}/workshops/free-tutorial-ws/intro',
            wait_until='domcontentloaded',
        )

        breadcrumb = page.locator('[data-testid="page-breadcrumb"]')
        breadcrumb.wait_for(state='visible')
        sidebar_nav = page.locator('[data-testid="workshop-sidebar"]')
        assert sidebar_nav.is_visible()

        padding_top = _band_padding_top(page)
        assert padding_top == EXPECTED_PADDING_PX

        page.set_viewport_size({'width': 390, 'height': 844})
        page.goto(
            f'{django_server}/workshops/free-tutorial-ws/intro',
            wait_until='domcontentloaded',
        )
        toggle = page.locator('[data-testid="reader-mobile-drawer-toggle"]')
        toggle.wait_for(state='visible')
        mobile_padding_top = _band_padding_top(page)
        assert mobile_padding_top == EXPECTED_PADDING_PX


# ----------------------------------------------------------------------
# Scenario 3: Free/anonymous visitor hits a tier-gated workshop
# tutorial page — the gated card still renders correctly below the
# tightened band, and the gated mobile nav toggle still renders.
# ----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestGatedWorkshopTutorial:
    def _setup(self):
        _clear_workshops()
        # pages=10 (Basic) gates the tutorial body for a free/anonymous
        # visitor, matching the pattern used by test_workshop_teaser_gating.
        workshop = _create_workshop(
            slug='gated-tutorial-ws',
            title='Gated Reader Band Workshop',
            landing=0,
            pages=10,
            recording=20,
            pages_data=[
                ('intro', 'Introduction', '# Welcome\n\nThis is the intro body.'),
            ],
        )
        _create_user('free-1677@test.com', tier_slug='free', email_verified=True)
        return workshop

    @browser_journey
    def test_gated_card_renders_correctly_below_tightened_band_desktop(
        self, django_server, browser,
    ):
        self._setup()
        ctx = _auth_context(browser, 'free-1677@test.com')
        try:
            page = ctx.new_page()
            page.set_viewport_size({'width': 1280, 'height': 900})
            page.goto(
                f'{django_server}/workshops/gated-tutorial-ws/intro',
                wait_until='domcontentloaded',
            )

            padding_top = _band_padding_top(page)
            assert padding_top == EXPECTED_PADDING_PX

            gated_card = page.locator('[data-testid="page-paywall"]')
            gated_card.wait_for(state='visible')
            cta = page.locator('[data-testid="page-upgrade-cta"]')
            assert cta.get_attribute('href') == '/membership'

            # No empty/broken appearance: the breadcrumb renders above
            # the card in the tightened band, not overlapping it.
            breadcrumb = page.locator('[data-testid="page-breadcrumb"]')
            breadcrumb.wait_for(state='visible')
            crumb_box = breadcrumb.bounding_box()
            card_box = gated_card.bounding_box()
            assert crumb_box is not None
            assert card_box is not None
            assert crumb_box['y'] < card_box['y']
        finally:
            ctx.close()

    @browser_journey
    def test_gated_mobile_nav_toggle_still_renders(self, django_server, browser):
        self._setup()
        ctx = _auth_context(browser, 'free-1677@test.com')
        try:
            page = ctx.new_page()
            page.set_viewport_size({'width': 390, 'height': 844})
            page.goto(
                f'{django_server}/workshops/gated-tutorial-ws/intro',
                wait_until='domcontentloaded',
            )

            mobile_padding_top = _band_padding_top(page)
            assert mobile_padding_top == EXPECTED_PADDING_PX

            gated_toggle = page.locator(
                '[data-testid="reader-mobile-nav-toggle-gated"]',
            )
            gated_toggle.wait_for(state='visible')
            gated_card = page.locator('[data-testid="page-paywall"]')
            gated_card.wait_for(state='visible')
        finally:
            ctx.close()


# ----------------------------------------------------------------------
# Scenario 4: Staff session confirms the floating "Edit in Studio"
# button still floats correctly (fixed, out-of-flow) after the padding
# change — it does not overlap or get clipped by the tightened band.
# ----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestStaffEditInStudioButtonUnaffected:
    def _setup(self):
        _clear_courses()
        course = _create_course(
            title='Staff Reader Band Course',
            slug='staff-reader-band-course',
            required_level=0,
            status='published',
        )
        module = _create_module(course, 'Module One', sort_order=0)
        unit = _create_unit(
            module, 'Unit One', sort_order=0, body='# Hello\n\nBody text.',
        )
        _create_staff_user('admin-1677@test.com')
        return unit

    @browser_journey
    def test_edit_in_studio_button_floats_correctly(self, django_server, browser):
        unit = self._setup()
        ctx = _auth_context(browser, 'admin-1677@test.com')
        try:
            page = ctx.new_page()
            page.set_viewport_size({'width': 1280, 'height': 900})
            page.goto(
                f'{django_server}{unit.get_absolute_url()}',
                wait_until='domcontentloaded',
            )

            padding_top = _band_padding_top(page)
            assert padding_top == EXPECTED_PADDING_PX

            edit_button = page.locator('[data-testid="studio-edit-button"]')
            edit_button.wait_for(state='visible')

            position = edit_button.evaluate(
                "el => getComputedStyle(el).position",
            )
            assert position == EDIT_BUTTON_EXPECTED_CSS_POSITION, (
                'Edit in Studio button should stay fixed/out-of-flow, '
                'unaffected by the reader band padding change.'
            )

            box = edit_button.bounding_box()
            assert box is not None
            # `fixed right-4 top-20` => 16px from the right edge, 80px
            # from the top, regardless of the reader band padding value.
            assert 900 <= box['x'] <= 1280, (
                f'Edit in Studio button x={box["x"]} should be near the '
                f'right edge of a 1280px viewport.'
            )
            assert 75 <= box['y'] <= 85, (
                f'Edit in Studio button y={box["y"]} should stay pinned '
                f'at the fixed top-20 offset (80px).'
            )

            # It must not overlap/clip the (now tighter) breadcrumb row.
            breadcrumb = page.locator('[data-reader-breadcrumb]')
            breadcrumb.wait_for(state='visible')
            crumb_box = breadcrumb.bounding_box()
            assert crumb_box is not None
            assert crumb_box['y'] >= box['y'], (
                'Breadcrumb should render at or below the floating Edit '
                'in Studio button, not clipped by it.'
            )
        finally:
            ctx.close()
