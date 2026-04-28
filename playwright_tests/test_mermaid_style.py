"""Playwright E2E tests for Mermaid styling fixes (issue #359).

Two fixes are exercised here:

1. Border visibility in dark mode: node borders must be visible against
   the dark card fill. We assert the rendered node `<rect>` has a
   `stroke` color distinct from its `fill` color in both dark and light
   palettes, including after a theme toggle.
2. Mobile-friendly sizing: at a 390px viewport, a wide diagram must
   render inside a horizontally scrollable container (`div.mermaid`
   itself), and the page itself must NOT scroll horizontally. On a
   desktop viewport with a small diagram that fits, the container must
   not introduce a redundant scrollbar.

Plus we re-confirm the lazy-load invariant from #300 still holds with
the new style block in place: pages with no mermaid blocks issue zero
requests to the Mermaid CDN.

Finally we capture screenshots for the QA agent at
``/tmp/mermaid-359-screenshots/``.

Usage:
    uv run python -m pytest playwright_tests/test_mermaid_style.py -v
"""

import os

import pytest

os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')

from playwright_tests.conftest import VIEWPORT  # noqa: E402
from playwright_tests.test_mermaid import (  # noqa: E402
    MERMAID_CDN_HOST,
    WORKSHOP_PLAIN_BODY,
)
from playwright_tests.test_mermaid_theme import (  # noqa: E402
    _add_localstorage_theme,
)
from playwright_tests.test_workshops import (  # noqa: E402
    _clear_workshops,
    _create_workshop,
)

SHOT_DIR = '/tmp/mermaid-359-screenshots'

# A deliberately wide flowchart so it overflows a 390px phone viewport
# but still has enough nodes to assert against. Eight chained nodes with
# longer labels guarantees a render width well above 390px even at the
# minimum mermaid font size.
WIDE_MERMAID_BODY = (
    "# Wide pipeline\n\n"
    "```mermaid\n"
    "flowchart LR\n"
    '    Ingest["Ingest raw events"] --> Validate["Validate schema"]\n'
    '    Validate --> Enrich["Enrich with profile"]\n'
    '    Enrich --> Score["Score with model"]\n'
    '    Score --> Decide["Decide routing"]\n'
    '    Decide --> Persist["Persist outcome"]\n'
    '    Persist --> Notify["Notify downstream"]\n'
    '    Notify --> Audit["Write audit log"]\n'
    "```\n"
)

# A small flowchart that fits inside a desktop card without scrolling.
NARROW_MERMAID_BODY = (
    "# Small\n\n"
    "```mermaid\n"
    "flowchart LR\n"
    '    A["A"] --> B["B"]\n'
    "```\n"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _wait_for_wide_diagram(page, timeout=15000):
    """Wait until at least one labelled node from WIDE_MERMAID_BODY is
    visible inside the rendered SVG. We pick a label that appears in the
    middle of the chain so the SVG must have been laid out before the
    assertion proceeds."""
    page.wait_for_function(
        """() => {
            const fos = document.querySelectorAll(
                'div.mermaid foreignObject'
            );
            const text = Array.from(fos)
                .map(n => n.textContent).join('|');
            return text.includes('Score with model')
                && text.includes('Persist outcome');
        }""",
        timeout=timeout,
    )


def _wait_for_narrow_diagram(page, timeout=15000):
    page.wait_for_function(
        """() => {
            const fos = document.querySelectorAll(
                'div.mermaid foreignObject'
            );
            const text = Array.from(fos)
                .map(n => n.textContent).join('|');
            return text.includes('A') && text.includes('B');
        }""",
        timeout=timeout,
    )


def _read_first_node_stroke_and_fill(page):
    """Return ``(stroke, fill)`` for the first node `<rect>` in the
    rendered SVG. We read both the SVG attribute and the resolved
    computed style and return whichever produced a non-empty value, so
    we don't care whether Mermaid emitted an inline style or a
    presentation attribute."""
    return page.evaluate(
        """() => {
            const r = document.querySelector(
                'div.mermaid svg .node rect'
            );
            if (!r) return null;
            const cs = getComputedStyle(r);
            const stroke = cs.stroke || r.getAttribute('stroke') || '';
            const fill = cs.fill || r.getAttribute('fill') || '';
            return { stroke: stroke, fill: fill };
        }"""
    )


# ---------------------------------------------------------------------------
# Scenario 1: dark-mode borders are visible against the card fill
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestDarkBorderVisible:
    """Reader on a dark-mode workshop diagram sees node outlines that
    are visibly distinct from the node fill (proxy for border visible)."""

    def test_dark_node_stroke_differs_from_fill(
        self, browser, django_server,
    ):
        _clear_workshops()
        _create_workshop(
            slug='dark-border-walkthrough',
            title='Dark Border Walkthrough',
            landing=0,
            pages=0,
            recording=0,
            pages_data=[
                ('arch', 'Arch', WIDE_MERMAID_BODY),
            ],
        )

        ctx = browser.new_context(viewport=VIEWPORT)
        _add_localstorage_theme(ctx, 'dark')
        page = ctx.new_page()
        try:
            page.goto(
                f'{django_server}'
                f'/workshops/dark-border-walkthrough/tutorial/arch',
                wait_until='domcontentloaded',
            )
            _wait_for_wide_diagram(page)

            assert page.evaluate(
                "() => document.documentElement.classList.contains('dark')"
            ) is True, 'expected <html> to have class=dark in dark mode'

            sf = _read_first_node_stroke_and_fill(page)
            assert sf is not None, 'no node rect found in rendered SVG'
            assert sf['stroke'], (
                f'node has empty stroke; expected accent-coloured '
                f'border. attrs={sf!r}'
            )
            assert sf['fill'], (
                f'node has empty fill; cannot compare to stroke. '
                f'attrs={sf!r}'
            )
            assert sf['stroke'] != sf['fill'], (
                f'in dark mode, node stroke must differ from fill so '
                f'the border is visible; got stroke={sf["stroke"]!r} '
                f'fill={sf["fill"]!r}'
            )
        finally:
            ctx.close()


# ---------------------------------------------------------------------------
# Scenario 2: light-mode borders remain visible (no regression)
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestLightBorderNotRegressed:
    """The fix must not regress light mode: node stroke must still
    differ from fill in light mode."""

    def test_light_node_stroke_differs_from_fill(
        self, browser, django_server,
    ):
        _clear_workshops()
        _create_workshop(
            slug='light-border-walkthrough',
            title='Light Border Walkthrough',
            landing=0,
            pages=0,
            recording=0,
            pages_data=[
                ('arch', 'Arch', WIDE_MERMAID_BODY),
            ],
        )

        ctx = browser.new_context(viewport=VIEWPORT)
        _add_localstorage_theme(ctx, 'light')
        page = ctx.new_page()
        try:
            page.goto(
                f'{django_server}'
                f'/workshops/light-border-walkthrough/tutorial/arch',
                wait_until='domcontentloaded',
            )
            _wait_for_wide_diagram(page)

            assert page.evaluate(
                "() => document.documentElement.classList.contains('dark')"
            ) is False, (
                'expected <html> to NOT have class=dark in light mode'
            )

            sf = _read_first_node_stroke_and_fill(page)
            assert sf is not None
            assert sf['stroke'] != sf['fill'], (
                f'in light mode, node stroke must still differ from '
                f'fill (no regression); got stroke={sf["stroke"]!r} '
                f'fill={sf["fill"]!r}'
            )
        finally:
            ctx.close()


# ---------------------------------------------------------------------------
# Scenario 3: theme-toggle round trip preserves visible borders
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestToggleRoundTripPreservesBorders:
    """After a light->dark->light round trip, borders must still be
    visible in both the intermediate dark state and the final light
    state."""

    def test_toggle_round_trip_keeps_stroke_distinct_from_fill(
        self, browser, django_server,
    ):
        _clear_workshops()
        _create_workshop(
            slug='border-roundtrip',
            title='Border Roundtrip',
            landing=0,
            pages=0,
            recording=0,
            pages_data=[
                ('arch', 'Arch', WIDE_MERMAID_BODY),
            ],
        )

        ctx = browser.new_context(viewport=VIEWPORT)
        _add_localstorage_theme(ctx, 'light')
        page = ctx.new_page()
        try:
            page.goto(
                f'{django_server}/workshops/border-roundtrip/tutorial/arch',
                wait_until='domcontentloaded',
            )
            _wait_for_wide_diagram(page)

            light_initial = _read_first_node_stroke_and_fill(page)
            assert light_initial['stroke'] != light_initial['fill']

            # Toggle to dark and wait for the rect's fill to actually
            # change (re-render fired).
            page.locator(
                '[data-testid="theme-toggle"]'
            ).first.click()
            page.wait_for_function(
                "() => document.documentElement.classList.contains('dark')",
                timeout=3000,
            )
            page.wait_for_function(
                """(prev) => {
                    const r = document.querySelector(
                        'div.mermaid svg .node rect'
                    );
                    if (!r) return false;
                    const f = getComputedStyle(r).fill
                        || r.getAttribute('fill') || '';
                    return f && f !== prev;
                }""",
                arg=light_initial['fill'],
                timeout=10000,
            )

            dark_state = _read_first_node_stroke_and_fill(page)
            assert dark_state['stroke'] != dark_state['fill'], (
                f'after toggle to dark, stroke must differ from fill; '
                f'got {dark_state!r}'
            )

            # Toggle back to light.
            page.locator(
                '[data-testid="theme-toggle"]'
            ).first.click()
            page.wait_for_function(
                "() => !document.documentElement.classList.contains('dark')",
                timeout=3000,
            )
            page.wait_for_function(
                """(prev) => {
                    const r = document.querySelector(
                        'div.mermaid svg .node rect'
                    );
                    if (!r) return false;
                    const f = getComputedStyle(r).fill
                        || r.getAttribute('fill') || '';
                    return f && f !== prev;
                }""",
                arg=dark_state['fill'],
                timeout=10000,
            )

            light_final = _read_first_node_stroke_and_fill(page)
            assert light_final['stroke'] != light_final['fill'], (
                f'after toggle back to light, stroke must still differ '
                f'from fill; got {light_final!r}'
            )
        finally:
            ctx.close()


# ---------------------------------------------------------------------------
# Scenario 4: 390px mobile viewport — wide diagram scrolls inside its
# container, page itself does NOT scroll horizontally.
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestMobileWideDiagramScrollsInPlace:
    def test_mobile_diagram_scrolls_within_container(
        self, browser, django_server,
    ):
        _clear_workshops()
        _create_workshop(
            slug='mobile-wide-walkthrough',
            title='Mobile Wide Walkthrough',
            landing=0,
            pages=0,
            recording=0,
            pages_data=[
                ('arch', 'Arch', WIDE_MERMAID_BODY),
            ],
        )

        ctx = browser.new_context(viewport={'width': 390, 'height': 844})
        _add_localstorage_theme(ctx, 'dark')
        page = ctx.new_page()
        try:
            page.goto(
                f'{django_server}'
                f'/workshops/mobile-wide-walkthrough/tutorial/arch',
                wait_until='domcontentloaded',
            )
            _wait_for_wide_diagram(page)

            sizes = page.evaluate(
                """() => {
                    const m = document.querySelector('div.mermaid');
                    return {
                        scrollWidth: m.scrollWidth,
                        clientWidth: m.clientWidth,
                        docScrollWidth: document.documentElement.scrollWidth,
                    };
                }"""
            )
            assert sizes['scrollWidth'] > sizes['clientWidth'], (
                f'div.mermaid must scroll horizontally on a 390px '
                f'viewport when the diagram is wider than the card; '
                f'got {sizes!r}'
            )
            assert sizes['docScrollWidth'] <= 390, (
                f'page itself must NOT scroll horizontally on a 390px '
                f'viewport; document.scrollWidth={sizes["docScrollWidth"]}'
            )

            # Programmatically scroll the diagram and confirm the
            # internal scroll position changes -- this is what a user
            # would experience when swiping.
            new_left = page.evaluate(
                """() => {
                    const m = document.querySelector('div.mermaid');
                    m.scrollLeft = 100;
                    return m.scrollLeft;
                }"""
            )
            assert new_left > 0, (
                f'div.mermaid scrollLeft must change when scrolled; '
                f'got {new_left!r}'
            )
        finally:
            ctx.close()


# ---------------------------------------------------------------------------
# Scenario 5: desktop with a small diagram has no redundant scrollbar
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestDesktopNarrowDiagramNoScrollbar:
    def test_desktop_narrow_diagram_does_not_scroll(
        self, browser, django_server,
    ):
        _clear_workshops()
        _create_workshop(
            slug='desktop-narrow-walkthrough',
            title='Desktop Narrow Walkthrough',
            landing=0,
            pages=0,
            recording=0,
            pages_data=[
                ('arch', 'Arch', NARROW_MERMAID_BODY),
            ],
        )

        ctx = browser.new_context(viewport=VIEWPORT)
        _add_localstorage_theme(ctx, 'dark')
        page = ctx.new_page()
        try:
            page.goto(
                f'{django_server}'
                f'/workshops/desktop-narrow-walkthrough/tutorial/arch',
                wait_until='domcontentloaded',
            )
            _wait_for_narrow_diagram(page)

            sizes = page.evaluate(
                """() => {
                    const m = document.querySelector('div.mermaid');
                    return {
                        scrollWidth: m.scrollWidth,
                        clientWidth: m.clientWidth,
                    };
                }"""
            )
            # When the SVG fits, the container should not introduce a
            # horizontal scrollbar. Allow exact equality.
            assert sizes['scrollWidth'] == sizes['clientWidth'], (
                f'on desktop with a small diagram, div.mermaid must '
                f'not have horizontal overflow; got {sizes!r}'
            )
        finally:
            ctx.close()


# ---------------------------------------------------------------------------
# Scenario 6: pages with no diagrams remain unaffected
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestPagesWithoutDiagramsUnaffected:
    """The new inline <style> block must not cause the partial to fetch
    the Mermaid CDN bundle on a page that has no diagrams. This is the
    same lazy-load invariant from #300, re-asserted post-#359."""

    def test_no_cdn_request_on_plain_page_after_359(
        self, browser, django_server,
    ):
        _clear_workshops()
        _create_workshop(
            slug='style-plain-walkthrough',
            title='Style Plain Walkthrough',
            landing=0,
            pages=0,
            recording=0,
            pages_data=[
                ('setup', 'Setup', WORKSHOP_PLAIN_BODY),
            ],
        )

        ctx = browser.new_context(viewport=VIEWPORT)
        page = ctx.new_page()

        cdn_requests = []
        page.on(
            'request',
            lambda req: cdn_requests.append(req.url)
            if MERMAID_CDN_HOST in req.url else None,
        )
        try:
            page.goto(
                f'{django_server}'
                f'/workshops/style-plain-walkthrough/tutorial/setup',
                wait_until='domcontentloaded',
            )
            page.wait_for_load_state('networkidle', timeout=3000)

            # Sanity: no diagrams on this page.
            assert page.locator('div.mermaid').count() == 0
            assert cdn_requests == [], (
                f'expected zero requests to {MERMAID_CDN_HOST} on a '
                f'page with no diagrams; got {cdn_requests!r}'
            )
        finally:
            ctx.close()


# ---------------------------------------------------------------------------
# Scenario 7: capture screenshots for QA
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestQAScreenshots:
    """Capture four screenshots (dark+light × desktop+390px) so the QA
    agent and product reviewer can eyeball the result."""

    def _setup(self):
        _clear_workshops()
        _create_workshop(
            slug='shot359-walkthrough',
            title='Shot 359 Walkthrough',
            landing=0,
            pages=0,
            recording=0,
            pages_data=[
                ('arch', 'Arch', WIDE_MERMAID_BODY),
            ],
        )

    def _shoot(self, browser, django_server, theme, viewport, filename):
        os.makedirs(SHOT_DIR, exist_ok=True)
        ctx = browser.new_context(viewport=viewport)
        _add_localstorage_theme(ctx, theme)
        page = ctx.new_page()
        try:
            page.goto(
                f'{django_server}'
                f'/workshops/shot359-walkthrough/tutorial/arch',
                wait_until='domcontentloaded',
            )
            _wait_for_wide_diagram(page)
            page.locator('div.mermaid').first.screenshot(
                path=f'{SHOT_DIR}/{filename}'
            )
        finally:
            ctx.close()

    def test_screenshot_dark_desktop(self, browser, django_server):
        self._setup()
        self._shoot(
            browser, django_server, 'dark', VIEWPORT,
            '01-dark-desktop.png',
        )

    def test_screenshot_dark_mobile(self, browser, django_server):
        self._setup()
        self._shoot(
            browser, django_server, 'dark',
            {'width': 390, 'height': 844},
            '02-dark-mobile-390.png',
        )

    def test_screenshot_light_desktop(self, browser, django_server):
        self._setup()
        self._shoot(
            browser, django_server, 'light', VIEWPORT,
            '03-light-desktop.png',
        )

    def test_screenshot_light_mobile(self, browser, django_server):
        self._setup()
        self._shoot(
            browser, django_server, 'light',
            {'width': 390, 'height': 844},
            '04-light-mobile-390.png',
        )
