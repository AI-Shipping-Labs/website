"""Playwright coverage for testimonial card height unification (Issue #511).

The shared partial ``templates/includes/testimonial_cards.html`` renders the
homepage and course-detail testimonial sections. Issue #511 caps each
``<blockquote>`` at ``line-clamp-10`` so cards in the same desktop row land at
the same height regardless of quote length, while staying tall enough that
the current corpus (longest quote 364 chars / 10 rendered lines on mobile
393px) does not get truncated on either viewport.

Why ``line-clamp-10`` and not ``line-clamp-8``: the issue grooming
calibrated against an estimate of "8 lines on mobile" for the longest
quote. Empirical measurement at viewport 393px (Chromium / Inter 14px /
``max-w-5xl`` 2-col grid -> single column carousel slot of
``min(84vw, 24rem)``) shows John's 364-char quote actually wraps to 10
lines, and Yan's 328-char quote to 9. Clamping at 8 truncates both. The
spec's binding acceptance criteria (``no current testimonial renders
truncated at mobile 393x851``) require the cap to clear the corpus, so
N=10 is the minimum value that satisfies all current testimonials and
still caps far-larger future submissions.

Tests in this file:

* ``test_homepage_desktop_row_heights_equal`` -- on desktop 1280x900 every
  pair of cards in the same horizontal row reaches the same pixel height.
* ``test_homepage_desktop_no_truncation_for_current_corpus`` -- no current
  testimonial is clipped at the 10-line cap on desktop.
* ``test_homepage_mobile_carousel_no_truncation_for_current_corpus`` -- same
  guarantee on mobile 393x851.
* ``test_course_detail_uses_shared_partial_with_clamp`` -- a course page
  with testimonials renders the same partial and the clamp is in effect on
  its blockquote.
* ``test_long_quote_clamps_at_ten_lines_on_course_detail`` -- a synthetic
  ~1500-char quote is clipped exactly at 10 lines and the author block
  stays rendered below it.
"""

import os
from pathlib import Path

import pytest

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")


SCREENSHOT_DIR = Path("playwright_tests/screenshots/issue-511")

# 1px tolerance for sub-pixel rounding in `getBoundingClientRect`.
HEIGHT_TOLERANCE_PX = 1

CLAMP_LINES = 10

# Long quote used to verify the clamp actually trims content.  Chosen well
# above any plausible line count at desktop 1280 so the 10-line cap activates.
LONG_QUOTE = (
    "This testimonial is intentionally far longer than any real quote in "
    "the current homepage corpus. It exists so the rendered blockquote "
    "exceeds ten lines at every supported viewport, which is the only "
    "way to verify that the line-clamp utility actually trims content. "
    "Without a quote of this length the clamp would be invisible because "
    "every real testimonial fits inside the cap. We deliberately repeat "
    "filler sentences so the clamp activates instead of relying on a single "
    "huge run-on. Filler sentence one. Filler sentence two. Filler sentence "
    "three. Filler sentence four. Filler sentence five. Filler sentence "
    "six. Filler sentence seven. Filler sentence eight. Filler sentence "
    "nine. Filler sentence ten. Filler sentence eleven. Filler sentence "
    "twelve. Filler sentence thirteen. Filler sentence fourteen. Filler "
    "sentence fifteen. Filler sentence sixteen. Filler sentence seventeen. "
    "Filler sentence eighteen. Filler sentence nineteen. Filler sentence "
    "twenty. Filler sentence twenty-one. Filler sentence twenty-two."
)


def _ensure_long_quote_course():
    """Create a published course with one long-quote testimonial (#511)."""
    from django.db import connection

    from content.models import Course, Module, Unit

    course, _ = Course.objects.update_or_create(
        slug="testimonial-clamp-511",
        defaults={
            "title": "Testimonial Clamp Course",
            "description": "Course used to verify the 10-line clamp.",
            "status": "published",
            "required_level": 0,
            "testimonials": [
                {
                    "quote": LONG_QUOTE,
                    "name": "Long Quote Reviewer",
                    "role": "Senior Tester",
                    "company": "Clamp Labs",
                    "source_url": "https://example.com/long-quote-source",
                },
                {
                    "quote": "Short and to the point.",
                    "name": "Brief Reviewer",
                    "role": "QA",
                    "company": "Clamp Labs",
                },
            ],
        },
    )
    module, _ = Module.objects.get_or_create(
        course=course,
        slug="intro",
        defaults={"title": "Intro", "sort_order": 1},
    )
    Unit.objects.get_or_create(
        module=module,
        slug="welcome",
        defaults={"title": "Welcome", "sort_order": 1},
    )
    connection.close()


def _scroll_section_into_view(page):
    """Scroll the testimonial grid into view, hiding the sticky header."""
    section = page.locator('[data-testid="testimonial-grid"]').first
    page.add_style_tag(
        content="header, #section-nav { visibility: hidden !important; }"
    )
    section.evaluate(
        "el => window.scrollTo(0, el.getBoundingClientRect().top + window.scrollY - 140)"
    )
    return section


def _screenshot_section(page, name):
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    section = _scroll_section_into_view(page)
    section.screenshot(path=SCREENSHOT_DIR / f"{name}.png")


def _card_geometry(page):
    """Return [(x, y, width, height)] for every testimonial card."""
    return page.locator('[data-testid="testimonial-card"]').evaluate_all(
        """els => els.map(el => {
            const r = el.getBoundingClientRect();
            return [r.x, r.y, r.width, r.height];
        })"""
    )


def _quote_truncation(page):
    """Return per-quote measurements indicating whether the clamp is active.

    A quote is "clamped" when its content overflows the visible box -- the
    standard CSS line-clamp signal. We measure it by comparing
    ``scrollHeight`` against ``clientHeight``; if scrollHeight is bigger,
    text is hidden behind the clamp (i.e. the ellipsis is visible). The
    visible line count is derived from clientHeight / line-height so a
    pure assertion like ``visible_lines <= CLAMP_LINES`` can hold across
    viewports.
    """
    return page.locator('[data-testid="testimonial-quote"]').evaluate_all(
        """els => els.map(el => {
            const cs = getComputedStyle(el);
            const lineHeight = parseFloat(cs.lineHeight);
            const visibleLines = Math.round(el.clientHeight / lineHeight);
            return {
                clamped: el.scrollHeight - el.clientHeight > 1,
                line_height: lineHeight,
                client_height: el.clientHeight,
                scroll_height: el.scrollHeight,
                visible_lines: visibleLines,
                webkit_line_clamp: cs.webkitLineClamp || cs.getPropertyValue('-webkit-line-clamp'),
                display: cs.display,
            };
        })"""
    )


def _assert_clamp_utility_applied(measurements, viewport_label):
    """Verify the line-clamp-10 utility is actually applied to each quote."""
    for i, m in enumerate(measurements):
        # The Tailwind CDN may serve `display: flow-root` along with
        # `-webkit-line-clamp: N`; modern browsers honour the line-clamp
        # property without `display: -webkit-box`. The binding signal is
        # the ``-webkit-line-clamp`` value -- if that is absent we know
        # the utility class is not in effect.
        clamp_value = (m["webkit_line_clamp"] or "").strip()
        assert clamp_value == str(CLAMP_LINES), (
            f"Testimonial #{i} ({viewport_label}) blockquote should have "
            f"-webkit-line-clamp: {CLAMP_LINES}, got {m['webkit_line_clamp']!r}. "
            f"line-clamp-{CLAMP_LINES} utility may be missing. "
            f"Full measurement: {m}"
        )


# ---------------------------------------------------------------------------
# Homepage -- desktop
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_homepage_desktop_row_heights_equal(django_server, page):
    """Each row of the homepage 2-col testimonial grid has equal-height cards."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(f"{django_server}/", wait_until="networkidle")
    _scroll_section_into_view(page)

    geometries = _card_geometry(page)
    assert len(geometries) >= 4, (
        f"Expected the homepage corpus to render >= 4 testimonials, "
        f"got {len(geometries)}"
    )

    # Group cards by row using y-coordinate (within tolerance), then assert
    # heights inside each row match within HEIGHT_TOLERANCE_PX.
    rows: dict[int, list[tuple[float, float, float, float]]] = {}
    for x, y, w, h in geometries:
        bucket = round(y / 4) * 4  # 4px row-bucket - survives sub-pixel jitter
        rows.setdefault(bucket, []).append((x, y, w, h))

    multi_card_rows = [r for r in rows.values() if len(r) >= 2]
    assert multi_card_rows, (
        "Expected at least one row with >= 2 cards on desktop "
        "(2-col grid). Geometries: " + repr(geometries)
    )

    for row in multi_card_rows:
        heights = [h for (_x, _y, _w, h) in row]
        spread = max(heights) - min(heights)
        assert spread <= HEIGHT_TOLERANCE_PX, (
            f"Cards in the same row must have equal heights (within "
            f"{HEIGHT_TOLERANCE_PX}px). Got heights={heights} "
            f"(spread={spread}px)."
        )

    _screenshot_section(page, "homepage-desktop-1280x900")


@pytest.mark.django_db
def test_homepage_desktop_no_truncation_for_current_corpus(django_server, page):
    """No current homepage testimonial is clipped at desktop 1280x900."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(f"{django_server}/", wait_until="networkidle")
    _scroll_section_into_view(page)

    measurements = _quote_truncation(page)
    assert measurements, "No testimonial quotes rendered on the homepage"
    _assert_clamp_utility_applied(measurements, "desktop 1280x900")

    for i, m in enumerate(measurements):
        assert not m["clamped"], (
            f"Testimonial #{i} on homepage desktop is clamped (truncated). "
            f"Measurement: {m}. The {CLAMP_LINES}-line cap should accommodate "
            f"the current corpus on desktop."
        )

    _screenshot_section(page, "homepage-desktop-no-truncation")


# ---------------------------------------------------------------------------
# Homepage -- mobile (carousel)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_homepage_mobile_carousel_no_truncation_for_current_corpus(
    django_server, page
):
    """No current homepage testimonial is clipped at mobile 393x851."""
    page.set_viewport_size({"width": 393, "height": 851})
    page.goto(f"{django_server}/", wait_until="networkidle")
    _scroll_section_into_view(page)

    measurements = _quote_truncation(page)
    assert measurements, "No testimonial quotes rendered on mobile"
    _assert_clamp_utility_applied(measurements, "mobile 393x851")

    for i, m in enumerate(measurements):
        assert not m["clamped"], (
            f"Testimonial #{i} on mobile (393x851) is clamped (truncated). "
            f"Measurement: {m}. The {CLAMP_LINES}-line cap should accommodate "
            f"the current corpus on mobile too."
        )
        assert m["visible_lines"] <= CLAMP_LINES, (
            f"Testimonial #{i} renders {m['visible_lines']} visible lines "
            f"-- the {CLAMP_LINES}-line cap should keep this <= {CLAMP_LINES}. "
            f"Measurement: {m}"
        )

    # Mobile carousel cards do not need to share a height -- they are
    # individually snap-aligned. The clamp only prevents extreme outliers
    # from breaking the rhythm. Verify each card sits in the snap row
    # (single horizontally-scrollable row).
    geometries = _card_geometry(page)
    ys = sorted({round(y / 4) * 4 for (_x, y, _w, _h) in geometries})
    assert len(ys) == 1, (
        f"Mobile carousel cards should share a y-coordinate (single row "
        f"horizontally scrollable). Got distinct y-buckets: {ys}"
    )

    _screenshot_section(page, "homepage-mobile-393x851")


# ---------------------------------------------------------------------------
# Course detail -- shared partial
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_course_detail_uses_shared_partial_with_clamp(django_server, page):
    """Course detail renders the same partial and inherits the clamp."""
    _ensure_long_quote_course()

    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(
        f"{django_server}/courses/testimonial-clamp-511",
        wait_until="networkidle",
    )

    grid = page.locator('[data-testid="testimonial-grid"]').first
    cards = page.locator('[data-testid="testimonial-card"]')

    assert grid.is_visible(), "Testimonial grid not rendered on course detail"
    assert cards.count() == 2, (
        f"Expected 2 testimonial cards on the seeded course, "
        f"got {cards.count()}"
    )

    measurements = _quote_truncation(page)
    _assert_clamp_utility_applied(
        measurements, "course detail desktop 1280x900"
    )

    _screenshot_section(page, "course-detail-desktop-1280x900")


@pytest.mark.django_db(transaction=True)
def test_long_quote_clamps_at_ten_lines_on_course_detail(django_server, page):
    """A 1500-char quote is clipped at 10 lines, author block still renders."""
    _ensure_long_quote_course()

    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(
        f"{django_server}/courses/testimonial-clamp-511",
        wait_until="networkidle",
    )

    measurements = _quote_truncation(page)
    quotes = page.locator('[data-testid="testimonial-quote"]')
    assert quotes.count() == 2

    # Find the long-quote card by the marker text. Index-by-content rather
    # than fixed position so future seed-order tweaks don't break the test.
    long_quote_index = None
    for i in range(quotes.count()):
        text = quotes.nth(i).inner_text()
        if "intentionally far longer" in text:
            long_quote_index = i
            break
    assert long_quote_index is not None, (
        "Could not locate the long-quote card by content"
    )

    long_m = measurements[long_quote_index]
    assert long_m["clamped"], (
        f"The long quote should be visibly clamped. Measurement: {long_m}"
    )
    assert long_m["visible_lines"] == CLAMP_LINES, (
        f"The long quote should display exactly {CLAMP_LINES} visible lines. "
        f"Got {long_m['visible_lines']}. Measurement: {long_m}"
    )
    assert long_m["scroll_height"] > long_m["client_height"], (
        f"The long quote's scroll height should exceed client height "
        f"because the clamp hides the overflow. Measurement: {long_m}"
    )

    # Author block on the same card must still be rendered (anchored at the
    # bottom of the card via `flex-1` on the blockquote).
    cards = page.locator('[data-testid="testimonial-card"]')
    author = cards.nth(long_quote_index).locator(
        '[data-testid="testimonial-author"]'
    )
    assert author.is_visible(), (
        "Author block must remain visible below the clamped quote"
    )

    # Source link on the long-quote card stays focusable + has the right href.
    source_link = author.locator("a").first
    assert source_link.get_attribute("href") == (
        "https://example.com/long-quote-source"
    )
    source_link.focus()
    assert source_link.evaluate("el => document.activeElement === el")

    # No horizontal overflow on the clipped card.
    overflows = cards.evaluate_all(
        "els => els.filter(el => el.scrollWidth > el.clientWidth + 1).length"
    )
    assert overflows == 0, (
        f"Cards should not overflow horizontally. Found {overflows} overflowing"
    )

    _screenshot_section(page, "course-detail-long-quote-clamped")
