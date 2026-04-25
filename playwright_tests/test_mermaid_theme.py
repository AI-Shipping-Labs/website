"""Playwright E2E tests for Mermaid theme support (issue #306).

Covers the BDD scenarios in the issue:

1. Reader on a dark-mode page sees a dark-themed diagram (node fill
   matches the site's dark `--card` token, edges use the lime accent).
2. Reader on a light-mode page sees a light-themed diagram (node fill
   matches the light `--card` token).
3. Toggling the theme re-renders the diagram in place -- no page reload,
   one SVG survives, content unchanged.
4. Toggling back to light restores the light palette.
5. Pages with no diagrams pay no Mermaid bandwidth in either theme even
   when the theme is toggled.
6. The XSS payload remains escaped after a theme switch -- no dialog
   fires, no literal `<script>alert(1)</script>` leaks into the DOM.

Usage:
    uv run python -m pytest playwright_tests/test_mermaid_theme.py -v
"""

import os

import pytest

os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')

from playwright_tests.conftest import VIEWPORT  # noqa: E402
from playwright_tests.test_mermaid import (  # noqa: E402
    MERMAID_CDN_HOST,
    WORKSHOP_MERMAID_BODY,
    WORKSHOP_PLAIN_BODY,
    WORKSHOP_XSS_BODY,
)
from playwright_tests.test_workshops import (  # noqa: E402
    _clear_workshops,
    _create_workshop,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# Scripted-pre-paint init helper. The site's dark mode is decided BEFORE
# DOMContentLoaded by a blocking script in templates/base.html that reads
# localStorage. We use addInitScript so the value is set as soon as the
# document exists, well before the blocking script runs.
def _add_localstorage_theme(context, theme):
    context.add_init_script(
        "try { window.localStorage.setItem('theme', '" + theme + "'); } "
        "catch (e) {}"
    )
    # Pin matchMedia so prefers-color-scheme cannot override our value
    # if the headless OS reports the opposite.
    context.add_init_script(
        "window.matchMedia = function(q) {"
        "  return {"
        "    matches: false,"
        "    media: q,"
        "    addListener: function() {},"
        "    removeListener: function() {},"
        "    addEventListener: function() {},"
        "    removeEventListener: function() {},"
        "    dispatchEvent: function() { return false; }"
        "  };"
        "};"
    )


def _wait_for_mermaid_ready(page, timeout=15000):
    """Wait until Mermaid finishes the first render. Mermaid 10 stamps
    data-processed once per div, but it sets it BEFORE the SVG is fully
    populated; we instead wait for actual node labels to appear inside
    `<foreignObject>` since flowcharts render labels there."""
    page.wait_for_function(
        """() => {
            const fos = document.querySelectorAll(
                'div.mermaid foreignObject'
            );
            const text = Array.from(fos)
                .map(n => n.textContent).join('|');
            return text.includes('Frontend UI')
                && text.includes('FastAPI app')
                && text.includes('Agent loop');
        }""",
        timeout=timeout,
    )


def _read_node_fills(page):
    """Return the resolved fill colors of all node rectangles inside
    every rendered Mermaid SVG. We read getComputedStyle().fill rather
    than the raw `fill` attribute because Mermaid 10 renders shapes with
    inline style="fill:..." most of the time."""
    return page.evaluate(
        """() => {
            const rects = document.querySelectorAll(
                'div.mermaid svg .node rect, '
                + 'div.mermaid svg .node polygon, '
                + 'div.mermaid svg .node path'
            );
            return Array.from(rects).map(r => {
                const cs = getComputedStyle(r);
                return cs.fill || r.getAttribute('fill') || '';
            });
        }"""
    )


def _read_edge_strokes(page):
    """Return resolved stroke colors of every edge path in the diagram.
    Mermaid uses .flowchart-link / .edge-thickness-* classes for edges."""
    return page.evaluate(
        """() => {
            const paths = document.querySelectorAll(
                'div.mermaid svg path.flowchart-link, '
                + 'div.mermaid svg .edgePath path, '
                + 'div.mermaid svg path.edge-thickness-normal'
            );
            return Array.from(paths).map(p => {
                const cs = getComputedStyle(p);
                return cs.stroke || p.getAttribute('stroke') || '';
            });
        }"""
    )


def _parse_rgb(color):
    """Parse `rgb(r,g,b)` / `rgba(r,g,b,a)` / `#rrggbb` into (r,g,b).
    Returns None if the string is not a recognised color."""
    if not color:
        return None
    color = color.strip().lower()
    if color.startswith('rgb'):
        # rgb(255, 0, 128) or rgba(255,0,128,0.5)
        inside = color[color.index('(') + 1:color.rindex(')')]
        parts = [p.strip() for p in inside.split(',')]
        if len(parts) < 3:
            return None
        try:
            r = int(float(parts[0]))
            g = int(float(parts[1]))
            b = int(float(parts[2]))
        except ValueError:
            return None
        return (r, g, b)
    if color.startswith('#'):
        h = color[1:]
        if len(h) == 3:
            h = ''.join(c * 2 for c in h)
        if len(h) != 6:
            return None
        try:
            return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
        except ValueError:
            return None
    return None


def _rgb_to_hsl(rgb):
    """Convert (r,g,b) 0-255 to (h,s,l) where h in [0,360), s/l in [0,1]."""
    if rgb is None:
        return None
    r, g, b = rgb[0] / 255.0, rgb[1] / 255.0, rgb[2] / 255.0
    mx, mn = max(r, g, b), min(r, g, b)
    lightness = (mx + mn) / 2
    if mx == mn:
        h = 0.0
        s = 0.0
    else:
        d = mx - mn
        s = d / (2 - mx - mn) if lightness > 0.5 else d / (mx + mn)
        if mx == r:
            h = ((g - b) / d) + (6 if g < b else 0)
        elif mx == g:
            h = ((b - r) / d) + 2
        else:
            h = ((r - g) / d) + 4
        h *= 60
    return (h, s, lightness)


def _has_lightness_below(colors, threshold):
    """True if any color in `colors` parses to HSL with lightness < threshold."""
    for c in colors:
        hsl = _rgb_to_hsl(_parse_rgb(c))
        if hsl is not None and hsl[2] < threshold:
            return True
    return False


def _has_lightness_above(colors, threshold):
    for c in colors:
        hsl = _rgb_to_hsl(_parse_rgb(c))
        if hsl is not None and hsl[2] > threshold:
            return True
    return False


def _has_hue_in(colors, lo, hi):
    """True if any color has hue in [lo,hi] AND non-zero saturation
    (gray has hue 0 by convention -- we don't want to count it)."""
    for c in colors:
        hsl = _rgb_to_hsl(_parse_rgb(c))
        if hsl is None:
            continue
        h, s, _l = hsl
        if s > 0.2 and lo <= h <= hi:
            return True
    return False


# ---------------------------------------------------------------------------
# Scenario 1: dark-mode page renders a dark-themed diagram
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestDarkModeDiagram:
    """Reader with localStorage.theme='dark' sees a dark canvas with
    accent-colored edges."""

    def test_dark_mode_node_fill_is_dark(
        self, browser, django_server,
    ):
        _clear_workshops()
        _create_workshop(
            slug='theme-dark-walkthrough',
            title='Theme Dark Walkthrough',
            landing=0,
            pages=0,
            recording=0,
            pages_data=[
                ('arch', 'Arch', WORKSHOP_MERMAID_BODY),
            ],
        )

        ctx = browser.new_context(viewport=VIEWPORT)
        _add_localstorage_theme(ctx, 'dark')
        page = ctx.new_page()
        try:
            page.goto(
                f'{django_server}'
                f'/workshops/theme-dark-walkthrough/tutorial/arch',
                wait_until='domcontentloaded',
            )
            _wait_for_mermaid_ready(page)

            # <html> carries the dark class.
            assert page.evaluate(
                "() => document.documentElement.classList.contains('dark')"
            ) is True, 'expected <html> to have class=dark in dark mode'

            fills = _read_node_fills(page)
            assert fills, 'no node shapes found in rendered SVG'
            assert _has_lightness_below(fills, 0.30), (
                f'expected at least one node fill with lightness < 30% in '
                f'dark mode, got {fills!r}'
            )

            strokes = _read_edge_strokes(page)
            assert strokes, 'no edge paths found in rendered SVG'
            # Lime accent is hsl(75 100% 50%) in dark mode -- hue 75.
            # Allow a 5-degree window for color-space rounding.
            assert _has_hue_in(strokes, 70, 80), (
                f'expected at least one edge stroke with hue in 70-80 '
                f'(lime accent), got {strokes!r}'
            )
        finally:
            ctx.close()


# ---------------------------------------------------------------------------
# Scenario 2: light-mode page renders a light-themed diagram
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestLightModeDiagram:
    """Reader with localStorage.theme='light' sees a light canvas with
    the same labels."""

    def test_light_mode_node_fill_is_light(
        self, browser, django_server,
    ):
        _clear_workshops()
        _create_workshop(
            slug='theme-light-walkthrough',
            title='Theme Light Walkthrough',
            landing=0,
            pages=0,
            recording=0,
            pages_data=[
                ('arch', 'Arch', WORKSHOP_MERMAID_BODY),
            ],
        )

        ctx = browser.new_context(viewport=VIEWPORT)
        _add_localstorage_theme(ctx, 'light')
        page = ctx.new_page()
        try:
            page.goto(
                f'{django_server}'
                f'/workshops/theme-light-walkthrough/tutorial/arch',
                wait_until='domcontentloaded',
            )
            _wait_for_mermaid_ready(page)

            assert page.evaluate(
                "() => document.documentElement.classList.contains('dark')"
            ) is False, (
                'expected <html> to NOT have class=dark in light mode'
            )

            fills = _read_node_fills(page)
            assert fills, 'no node shapes found in rendered SVG'
            assert _has_lightness_above(fills, 0.90), (
                f'expected at least one node fill with lightness > 90% in '
                f'light mode, got {fills!r}'
            )

            # Same labels are still present (proves the diagram itself
            # rendered, not just a blank canvas in the right palette).
            label_text = page.evaluate(
                """() => Array.from(
                    document.querySelectorAll('div.mermaid foreignObject')
                ).map(n => n.textContent).join('|')"""
            )
            assert 'Frontend UI' in label_text
            assert 'FastAPI app' in label_text
            assert 'Agent loop' in label_text
        finally:
            ctx.close()


# ---------------------------------------------------------------------------
# Scenario 3: theme toggle re-renders without a reload
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestThemeToggleRerendersDiagram:
    """Clicking the toggle on a page with a rendered diagram swaps the
    palette in place -- no navigation, content survives, exactly one SVG."""

    def test_toggle_swaps_palette_without_reload(
        self, browser, django_server,
    ):
        _clear_workshops()
        _create_workshop(
            slug='theme-toggle-walkthrough',
            title='Theme Toggle Walkthrough',
            landing=0,
            pages=0,
            recording=0,
            pages_data=[
                ('arch', 'Arch', WORKSHOP_MERMAID_BODY),
            ],
        )

        ctx = browser.new_context(viewport=VIEWPORT)
        _add_localstorage_theme(ctx, 'light')
        page = ctx.new_page()
        try:
            page.goto(
                f'{django_server}'
                f'/workshops/theme-toggle-walkthrough/tutorial/arch',
                wait_until='domcontentloaded',
            )
            _wait_for_mermaid_ready(page)

            # Stamp a sentinel on window so we can detect a navigation:
            # if the page reloads, our property goes away.
            page.evaluate(
                "() => { window.__mermaidNoReload = 'still-here'; }"
            )

            light_fills = _read_node_fills(page)
            assert light_fills, 'no node shapes found before toggle'
            light_first_fill = light_fills[0]

            # Click the visible (desktop) toggle.
            page.locator(
                '[data-testid="theme-toggle"]'
            ).first.click()

            # Wait for <html> to gain dark class.
            page.wait_for_function(
                "() => document.documentElement.classList.contains('dark')",
                timeout=3000,
            )

            # Wait for the rendered SVG to actually pick up the new
            # palette -- node fill must differ from the recorded light
            # value. This proves the re-render fired.
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
                arg=light_first_fill,
                timeout=10000,
            )

            # No reload happened: our sentinel is still there.
            assert page.evaluate(
                "() => window.__mermaidNoReload"
            ) == 'still-here', (
                'page must not have reloaded during the theme toggle'
            )

            # New palette is dark.
            dark_fills = _read_node_fills(page)
            assert _has_lightness_below(dark_fills, 0.30), (
                f'after toggle to dark, expected a node fill with '
                f'lightness < 30%, got {dark_fills!r}'
            )

            # Labels survived the re-render.
            label_text = page.evaluate(
                """() => Array.from(
                    document.querySelectorAll('div.mermaid foreignObject')
                ).map(n => n.textContent).join('|')"""
            )
            assert 'Frontend UI' in label_text
            assert 'FastAPI app' in label_text
            assert 'Agent loop' in label_text

            # Exactly one SVG inside the mermaid div -- no orphaned
            # leftover from the first render, no double-render.
            svg_count = page.evaluate(
                "() => document.querySelectorAll("
                "'div.mermaid > svg').length"
            )
            assert svg_count == 1, (
                f'expected exactly 1 svg inside div.mermaid, got '
                f'{svg_count}'
            )
        finally:
            ctx.close()


# ---------------------------------------------------------------------------
# Scenario 4: toggling back to light restores the light palette
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestThemeToggleRoundTrip:
    """A round-trip dark -> light brings back the light palette and
    triggers no XSS dialog along the way."""

    def test_toggle_back_to_light(self, browser, django_server):
        _clear_workshops()
        _create_workshop(
            slug='theme-roundtrip-walkthrough',
            title='Theme Roundtrip Walkthrough',
            landing=0,
            pages=0,
            recording=0,
            pages_data=[
                ('arch', 'Arch', WORKSHOP_MERMAID_BODY),
            ],
        )

        ctx = browser.new_context(viewport=VIEWPORT)
        _add_localstorage_theme(ctx, 'dark')
        page = ctx.new_page()

        dialogs = []

        def _on_dialog(d):
            dialogs.append(d.message)
            d.dismiss()

        page.on('dialog', _on_dialog)
        try:
            page.goto(
                f'{django_server}'
                f'/workshops/theme-roundtrip-walkthrough/tutorial/arch',
                wait_until='domcontentloaded',
            )
            _wait_for_mermaid_ready(page)

            dark_fills = _read_node_fills(page)
            assert dark_fills
            dark_first = dark_fills[0]

            # Toggle to light.
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
                arg=dark_first,
                timeout=10000,
            )

            light_fills = _read_node_fills(page)
            assert _has_lightness_above(light_fills, 0.90), (
                f'after toggle back to light, expected a node fill with '
                f'lightness > 90%, got {light_fills!r}'
            )

            assert dialogs == [], (
                f'no dialog should fire during a normal theme round-trip; '
                f'got: {dialogs!r}'
            )
        finally:
            ctx.close()


# ---------------------------------------------------------------------------
# Scenario 5: pages without diagrams pay no Mermaid bandwidth in either theme
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestNoDiagramNoBandwidthAcrossThemes:
    """The lazy-load invariant from #300 must still hold after #306:
    pages with zero <div class=\"mermaid\"> never fetch the CDN bundle,
    even after a theme toggle."""

    def test_no_cdn_request_after_toggle_on_plain_page(
        self, browser, django_server,
    ):
        _clear_workshops()
        _create_workshop(
            slug='theme-plain-walkthrough',
            title='Theme Plain Walkthrough',
            landing=0,
            pages=0,
            recording=0,
            pages_data=[
                # Diagram page exists but we never visit it.
                ('arch', 'Arch', WORKSHOP_MERMAID_BODY),
                # Plain page is what we visit.
                ('setup', 'Setup', WORKSHOP_PLAIN_BODY),
            ],
        )

        ctx = browser.new_context(viewport=VIEWPORT)
        _add_localstorage_theme(ctx, 'dark')
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
                f'/workshops/theme-plain-walkthrough/tutorial/setup',
                wait_until='domcontentloaded',
            )
            page.wait_for_timeout(500)

            # Sanity: no diagrams on this page.
            assert page.locator('div.mermaid').count() == 0

            # Toggle theme -- still no CDN fetch.
            page.locator(
                '[data-testid="theme-toggle"]'
            ).first.click()
            page.wait_for_function(
                "() => !document.documentElement.classList.contains('dark')",
                timeout=3000,
            )
            page.wait_for_timeout(500)

            assert cdn_requests == [], (
                f'expected zero requests to {MERMAID_CDN_HOST} on a '
                f'page with no diagrams, even after a theme toggle; '
                f'got {cdn_requests!r}'
            )
        finally:
            ctx.close()


# ---------------------------------------------------------------------------
# Scenario 6: XSS payload remains escaped after a theme switch
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestXssStaysEscapedAfterRerender:
    """The #300 XSS scenario must still pass after a theme-driven
    re-render: securityLevel:'strict' is set on every initialize call."""

    def test_xss_does_not_execute_after_toggle(
        self, browser, django_server,
    ):
        _clear_workshops()
        _create_workshop(
            slug='theme-xss-walkthrough',
            title='Theme XSS Walkthrough',
            landing=0,
            pages=0,
            recording=0,
            pages_data=[
                ('xss', 'XSS', WORKSHOP_XSS_BODY),
            ],
        )

        ctx = browser.new_context(viewport=VIEWPORT)
        _add_localstorage_theme(ctx, 'light')
        page = ctx.new_page()

        dialogs = []

        def _on_dialog(d):
            dialogs.append(d.message)
            d.dismiss()

        page.on('dialog', _on_dialog)
        try:
            page.goto(
                f'{django_server}'
                f'/workshops/theme-xss-walkthrough/tutorial/xss',
                wait_until='domcontentloaded',
            )
            # Body is up.
            page.locator('[data-testid="page-body"]').wait_for(
                state='attached', timeout=2000,
            )
            # Give the initial render a chance to attempt to fire (or
            # safely fail if the payload makes Mermaid bail).
            page.wait_for_timeout(2000)

            # Toggle to trigger a re-render.
            page.locator(
                '[data-testid="theme-toggle"]'
            ).first.click()
            page.wait_for_function(
                "() => document.documentElement.classList.contains('dark')",
                timeout=3000,
            )
            # Allow the re-render to attempt.
            page.wait_for_timeout(2000)

            assert dialogs == [], (
                f'no dialog should fire during initial render or '
                f're-render; got: {dialogs!r}'
            )

            # The literal <script>alert(1)</script> must not appear in
            # the rendered DOM.
            html = page.content()
            assert '<script>alert(1)</script>' not in html, (
                'unescaped <script> leaked into the DOM after '
                'theme toggle re-render'
            )
        finally:
            ctx.close()
