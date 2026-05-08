"""Issue #512 — Mobile homepage `Workshops & Learning Materials` (#resources)
section overflows the Pixel 7 viewport.

The fix converts the section into a horizontal scroll-snap carousel on
`<md` (mirroring the testimonial cards pattern) and keeps the desktop
`md:grid-cols-2 lg:grid-cols-3` layout unchanged. Cards on mobile are
sized `w-[min(82vw,22rem)]`, the title gets `break-words` plus
`[overflow-wrap:anywhere]`, and the section has `overflow-x-hidden` as a
parent overflow guard.

Screenshots are written to ``playwright_tests/screenshots/issue-512/``.
"""

import datetime
import os
from pathlib import Path

import pytest
from django.db import connection
from django.utils import timezone

from playwright_tests.conftest import ensure_site_config_tiers

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

SCREENSHOT_DIR = Path("playwright_tests/screenshots/issue-512")
PIXEL_7 = {"width": 393, "height": 851}
IPHONE_SE = {"width": 375, "height": 812}
LEGACY_320 = {"width": 320, "height": 568}
TABLET_1024 = {"width": 1024, "height": 768}
LAPTOP_1280 = {"width": 1280, "height": 800}

LONG_TOKEN_TITLE = (
    "Workshop-Materials-And-Live-Coding-Walkthrough-On-Building-Multi-Agent-"
    "Retrieval-Augmented-Generation-Systems-2026"
)


def _clear_recordings():
    from events.models import Event

    Event.objects.all().delete()
    connection.close()


def _seed_recordings(n=4, long_title_index=None):
    """Create `n` published events with non-empty recording_url so they appear
    on the homepage `recordings` queryset (which filters published=True and
    recording_url != '').

    If `long_title_index` is given, that one's title is a single very long
    hyphenated token so we can test mid-token wrapping defenses.
    """
    from events.models import Event

    base_dt = timezone.now() - datetime.timedelta(days=1)
    for i in range(n):
        title = (
            LONG_TOKEN_TITLE
            if long_title_index is not None and i == long_title_index
            else f"Workshop on AI Agents Part {i + 1}"
        )
        Event.objects.create(
            slug=f"issue-512-recording-{i}",
            title=title,
            description=(
                f"Hands-on workshop session #{i + 1} with embedded content, "
                "timestamps, and follow-up materials."
            ),
            start_datetime=base_dt - datetime.timedelta(hours=i),
            status="completed",
            published=True,
            recording_url=f"https://www.youtube.com/watch?v=demo{i}",
        )
    connection.close()


def _screenshot(page, name):
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SCREENSHOT_DIR / f"{name}.png", full_page=True)


def _section_screenshot(page, selector, name):
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.locator(selector).first.screenshot(
        path=SCREENSHOT_DIR / f"{name}.png"
    )


def _doc_overflow(page):
    return page.evaluate(
        "() => document.documentElement.scrollWidth - "
        "document.documentElement.clientWidth"
    )


# ---------------------------------------------------------------------------
# Scenario 1: Pixel 7 visitor — no body overflow on the homepage
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_pixel7_no_horizontal_overflow_on_home(django_server, page):
    ensure_site_config_tiers()
    _clear_recordings()
    _seed_recordings(n=4)

    page.set_viewport_size(PIXEL_7)
    page.goto(f"{django_server}/", wait_until="networkidle")

    # The section exists and contains the carousel.
    page.locator("#resources").wait_for(state="attached")
    page.evaluate(
        "() => document.getElementById('resources')."
        "scrollIntoView({block:'start'})"
    )
    page.wait_for_timeout(150)

    # No Django template comments leaked into the rendered HTML. `{# ... #}`
    # is single-line only — multi-line `{# #}` blocks render literally and
    # show up as visible page text.
    body_html = page.content()
    assert "{# " not in body_html, (
        "Django `{# #}` comment leaked into rendered HTML — multi-line "
        "comments must use `{% comment %}` instead"
    )
    assert "Issue #512" not in body_html, (
        "Issue-512 marker text leaked into the rendered page"
    )

    # Body has no horizontal overflow.
    overflow = _doc_overflow(page)
    assert overflow <= 1, (
        f"document overflows by {overflow}px at Pixel 7 — #resources still "
        f"bursts the viewport"
    )

    # The #resources section's own scrollWidth equals its clientWidth (no
    # horizontal scroll on the section itself; the inner carousel is the
    # only scrollable region).
    section_dims = page.evaluate(
        """() => {
            const s = document.getElementById('resources');
            return { sw: s.scrollWidth, cw: s.clientWidth };
        }"""
    )
    assert section_dims["sw"] - section_dims["cw"] <= 1, (
        f"#resources section overflows: scrollWidth={section_dims['sw']}, "
        f"clientWidth={section_dims['cw']}"
    )

    _section_screenshot(page, "#resources", "393x851-default")


# ---------------------------------------------------------------------------
# Scenario 2: Long workshop titles wrap cleanly on a narrow phone
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_long_title_wraps_inside_card_on_pixel7(django_server, page):
    ensure_site_config_tiers()
    _clear_recordings()
    # Index 0 gets the long title; with start_datetime ordering the most
    # recent (i=0) shows up first.
    _seed_recordings(n=3, long_title_index=0)

    page.set_viewport_size(PIXEL_7)
    page.goto(f"{django_server}/", wait_until="networkidle")
    page.evaluate(
        "() => document.getElementById('resources')."
        "scrollIntoView({block:'start'})"
    )
    page.wait_for_timeout(150)

    # Body still doesn't overflow.
    overflow = _doc_overflow(page)
    assert overflow <= 1, (
        f"document overflows by {overflow}px when a long-token title is "
        f"present"
    )

    # Find the card whose <h3> contains the long token. Its right edge must
    # be inside the section's content area, and the <h3>'s scrollWidth must
    # not exceed its clientWidth (the title wraps inside the card).
    geom = page.evaluate(
        """token => {
            const section = document.getElementById('resources');
            const sr = section.getBoundingClientRect();
            const articles = section.querySelectorAll('article');
            for (const a of articles) {
                const h3 = a.querySelector('h3');
                if (!h3) continue;
                if (h3.textContent.indexOf(token) !== -1) {
                    const ar = a.getBoundingClientRect();
                    return {
                        articleRight: ar.right,
                        articleLeft: ar.left,
                        sectionRight: sr.right,
                        h3Scroll: h3.scrollWidth,
                        h3Client: h3.clientWidth,
                        h3Height: h3.getBoundingClientRect().height,
                    };
                }
            }
            return null;
        }""",
        LONG_TOKEN_TITLE,
    )
    assert geom is not None, "Could not locate the long-title card"
    # The card's right edge stays inside the section's right edge (allow
    # 1px slack for sub-pixel rounding).
    assert geom["articleRight"] <= geom["sectionRight"] + 1, (
        f"long-title card right ({geom['articleRight']}) exceeds section "
        f"right ({geom['sectionRight']})"
    )
    # The <h3> wraps inside the card (no horizontal scroll inside the
    # title element itself).
    assert geom["h3Scroll"] - geom["h3Client"] <= 1, (
        f"<h3> overflows its container: scrollWidth={geom['h3Scroll']}, "
        f"clientWidth={geom['h3Client']} — title is not wrapping"
    )
    # The wrapped title takes up multiple lines (the line-height of the
    # text-lg utility is ~28px; a wrapped 100-char token must be
    # noticeably taller than a single line).
    assert geom["h3Height"] > 40, (
        f"<h3> height {geom['h3Height']}px suggests no wrapping happened"
    )

    _section_screenshot(page, "#resources", "393x851-longtitle")


# ---------------------------------------------------------------------------
# Scenario 3: Mobile carousel scrolls horizontally and snaps
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_mobile_carousel_scrolls_internally(django_server, page):
    ensure_site_config_tiers()
    _clear_recordings()
    _seed_recordings(n=4)

    page.set_viewport_size(PIXEL_7)
    page.goto(f"{django_server}/", wait_until="networkidle")
    page.evaluate(
        "() => document.getElementById('resources')."
        "scrollIntoView({block:'start'})"
    )
    page.wait_for_timeout(150)

    # The carousel exists, is `flex`, has `overflow-x: auto`, and uses
    # `scroll-snap-type: x mandatory`.
    styles = page.evaluate(
        """() => {
            const el = document.querySelector(
                '[data-testid="home-recordings-carousel"]'
            );
            if (!el) return null;
            const cs = getComputedStyle(el);
            return {
                display: cs.display,
                overflowX: cs.overflowX,
                snapType: cs.scrollSnapType,
                scrollWidth: el.scrollWidth,
                clientWidth: el.clientWidth,
            };
        }"""
    )
    assert styles is not None, (
        "[data-testid=home-recordings-carousel] not found on page"
    )
    assert styles["display"] == "flex", (
        f"Mobile carousel display is {styles['display']}, expected flex"
    )
    assert styles["overflowX"] in ("auto", "scroll"), (
        f"Mobile carousel overflowX is {styles['overflowX']}, "
        f"expected auto/scroll"
    )
    assert "x" in styles["snapType"] and "mandatory" in styles["snapType"], (
        f"Mobile carousel scroll-snap-type is {styles['snapType']!r}, "
        f"expected `x mandatory`"
    )
    # The carousel itself must be wider than the viewport (multiple cards
    # in a row) — otherwise the horizontal scroll has nothing to scroll.
    assert styles["scrollWidth"] > styles["clientWidth"], (
        f"Carousel scrollWidth ({styles['scrollWidth']}) is not greater "
        f"than clientWidth ({styles['clientWidth']}) — cards may not be "
        f"laid out as a horizontal strip"
    )

    # Scrolling the carousel right does not scroll the page body.
    initial_doc_scroll = page.evaluate(
        "() => document.documentElement.scrollLeft"
    )
    page.evaluate(
        """() => {
            const c = document.querySelector(
                '[data-testid="home-recordings-carousel"]'
            );
            c.scrollLeft = c.clientWidth + 16;
        }"""
    )
    page.wait_for_timeout(250)
    state = page.evaluate(
        """() => {
            const c = document.querySelector(
                '[data-testid="home-recordings-carousel"]'
            );
            return {
                carouselScrollLeft: c.scrollLeft,
                docScrollLeft: document.documentElement.scrollLeft,
            };
        }"""
    )
    assert state["carouselScrollLeft"] > 0, (
        "Carousel did not actually scroll horizontally"
    )
    assert state["docScrollLeft"] == initial_doc_scroll, (
        f"Page body scrolled horizontally to "
        f"{state['docScrollLeft']} (was {initial_doc_scroll}); "
        f"section overflow leaked outside the carousel"
    )


# ---------------------------------------------------------------------------
# Scenario 4: iPhone SE width is overflow-free
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_iphone_se_no_horizontal_overflow(django_server, page):
    ensure_site_config_tiers()
    _clear_recordings()
    _seed_recordings(n=4)

    page.set_viewport_size(IPHONE_SE)
    page.goto(f"{django_server}/", wait_until="networkidle")
    overflow = _doc_overflow(page)
    assert overflow <= 1, (
        f"iPhone SE 375px viewport has document overflow of {overflow}px"
    )
    _screenshot(page, "375x812")


# ---------------------------------------------------------------------------
# Scenario 5: 320px legacy phone width is overflow-free
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_legacy_320_no_horizontal_overflow(django_server, page):
    ensure_site_config_tiers()
    _clear_recordings()
    _seed_recordings(n=4)

    page.set_viewport_size(LEGACY_320)
    page.goto(f"{django_server}/", wait_until="networkidle")
    overflow = _doc_overflow(page)
    assert overflow <= 1, (
        f"320px viewport has document overflow of {overflow}px"
    )
    _screenshot(page, "320x568")


# ---------------------------------------------------------------------------
# Scenario 6: Tablet (1024px) keeps the 2-column grid intact
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_tablet_keeps_grid(django_server, page):
    ensure_site_config_tiers()
    _clear_recordings()
    # The homepage view caps `recordings` at the 3 most-recent rows
    # (`content/views/home.py` `_public_home` line 165). Seeding more is a
    # waste; 3 already fills the desktop 3-column grid.
    _seed_recordings(n=3)

    page.set_viewport_size(TABLET_1024)
    page.goto(f"{django_server}/", wait_until="networkidle")
    page.evaluate(
        "() => document.getElementById('resources')."
        "scrollIntoView({block:'start'})"
    )
    page.wait_for_timeout(150)

    info = page.evaluate(
        """() => {
            const el = document.querySelector(
                '[data-testid="home-recordings-carousel"]'
            );
            const cs = getComputedStyle(el);
            return {
                display: cs.display,
                cols: cs.gridTemplateColumns.split(' ').length,
                overflowX: cs.overflowX,
            };
        }"""
    )
    assert info["display"] == "grid", (
        f"At 1024px the recordings container should be a grid, got "
        f"{info['display']}"
    )
    # At lg (>=1024px) Tailwind applies `lg:grid-cols-3`.
    assert info["cols"] == 3, (
        f"At 1024px expected 3 grid tracks (lg:grid-cols-3), got "
        f"{info['cols']}"
    )
    assert info["overflowX"] in ("visible", "clip"), (
        f"At 1024px the grid container's overflowX is {info['overflowX']!r}, "
        f"expected the desktop value (no horizontal scroll on desktop)"
    )

    _screenshot(page, "desktop-1024")


# ---------------------------------------------------------------------------
# Scenario 7: Laptop (1280px) keeps the 3-column grid intact
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_laptop_keeps_three_column_grid(django_server, page):
    ensure_site_config_tiers()
    _clear_recordings()
    # The homepage view caps `recordings` at the 3 most-recent rows; 3 is
    # exactly enough to fill the desktop 3-column grid.
    _seed_recordings(n=3)

    page.set_viewport_size(LAPTOP_1280)
    page.goto(f"{django_server}/", wait_until="networkidle")
    page.evaluate(
        "() => document.getElementById('resources')."
        "scrollIntoView({block:'start'})"
    )
    page.wait_for_timeout(150)

    info = page.evaluate(
        """() => {
            const el = document.querySelector(
                '[data-testid="home-recordings-carousel"]'
            );
            const cs = getComputedStyle(el);
            const articles = el.querySelectorAll('article');
            // Read the article border plus the clickable card body's padding
            // to confirm card chrome is preserved.
            const a = articles[0];
            const acs = a ? getComputedStyle(a) : null;
            const body = a ? a.querySelector('a') : null;
            const bcs = body ? getComputedStyle(body) : null;
            return {
                display: cs.display,
                cols: cs.gridTemplateColumns.split(' ').length,
                cardCount: articles.length,
                cardBorder: acs ? acs.borderTopWidth : null,
                cardBodyPadding: bcs ? bcs.paddingTop : null,
            };
        }"""
    )
    assert info["display"] == "grid"
    assert info["cols"] == 3, (
        f"At 1280px expected lg:grid-cols-3 (3 tracks), got {info['cols']}"
    )
    assert info["cardCount"] == 3
    # The card retains its existing 1px border and the clickable card body
    # retains its `p-6` (24px) padding.
    assert info["cardBorder"] == "1px"
    assert info["cardBodyPadding"] == "24px"

    _screenshot(page, "desktop-1280")


# ---------------------------------------------------------------------------
# Scenario 8: Empty-recordings state does not introduce overflow
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_empty_state_no_overflow_on_pixel7(django_server, page):
    ensure_site_config_tiers()
    _clear_recordings()  # zero recordings → empty-state branch renders.

    page.set_viewport_size(PIXEL_7)
    page.goto(f"{django_server}/", wait_until="networkidle")
    page.evaluate(
        "() => document.getElementById('resources')."
        "scrollIntoView({block:'start'})"
    )
    page.wait_for_timeout(150)

    # The empty-state copy is on screen.
    empty_text = page.locator(
        "#resources",
    ).get_by_text("New event recordings drop after each live session")
    assert empty_text.count() >= 1, (
        "Empty-state card not rendered when there are no recordings"
    )

    overflow = _doc_overflow(page)
    assert overflow <= 1, (
        f"Empty-state branch introduces document overflow of {overflow}px"
    )
