"""Section anchors on rendered content headings (issue #1833).

Headings rendered from markdown carry slug ids, so ``<url>#<slug>`` (and the
``/c/<uuid>#<slug>`` share-link form) lands on the section below the fixed
header. ``static/js/section-anchors.js`` adds the hover ``#`` link and carries
a fragment through the gated sign-in round trip via sessionStorage.
"""

import datetime
import os
import struct
import uuid
import zlib
from pathlib import Path

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import expect

from playwright_tests.conftest import (
    DEFAULT_PASSWORD,
    SETTLE_TIMEOUT_MS,
    auth_context,
    create_session_for_user,
    create_user,
    ensure_tiers,
)
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = [pytest.mark.local_only, pytest.mark.django_db(transaction=True)]

SCREENSHOT_DIR = Path(".tmp/screenshots/issue-1833")
DESKTOP = {"width": 1280, "height": 800}
PHONE = {"width": 390, "height": 844}
CLIPBOARD = ["clipboard-read", "clipboard-write"]


def _filler(label, paragraphs=8):
    return "\n\n".join(
        f"{label} paragraph {n}. " + ("Long lesson prose keeps going. " * 12)
        for n in range(paragraphs)
    )


LESSON_BODY = (
    "## Setup\n\n" + _filler("Setup") + "\n\n"
    "## Streaming Responses\n\n" + _filler("Streaming") + "\n\n"
    "## Error Handling\n\n" + _filler("Errors") + "\n"
)


def _capture(page, name):
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SCREENSHOT_DIR / f"{name}.png")


def _reset():
    from django.db import connection

    from content.models import Article, Course, MarketingPage, Workshop
    from events.models import Event
    from integrations.middleware import clear_announcement_banner_cache
    from integrations.models import AnnouncementBanner

    Course.objects.all().delete()
    Article.objects.all().delete()
    Workshop.objects.all().delete()
    MarketingPage.objects.all().delete()
    Event.objects.all().delete()
    AnnouncementBanner.objects.all().delete()
    clear_announcement_banner_cache()
    connection.close()


def _lesson(required_level=0, slug="anchors-course"):
    from django.db import connection

    from content.models import Course, Module, Unit

    course = Course.objects.create(
        title="Anchors Course",
        slug=slug,
        status="published",
        required_level=required_level,
    )
    module = Module.objects.create(
        course=course, title="Foundation", slug="foundation", sort_order=1,
    )
    content_id = uuid.uuid4()
    unit = Unit.objects.create(
        module=module,
        title="OpenAI API",
        slug="openai-api",
        sort_order=1,
        source_content_id=content_id,
        body=LESSON_BODY,
    )
    url = unit.get_absolute_url()
    connection.close()
    return content_id, url


def _article(slug, markdown, content_html=None):
    from django.db import connection

    from content.models import Article

    article = Article.objects.create(
        title=f"Article {slug}",
        slug=slug,
        description="Anchors article.",
        content_markdown=markdown,
        date=datetime.date(2026, 9, 1),
        required_level=0,
        published=True,
    )
    if content_html is not None:
        Article.objects.filter(pk=article.pk).update(content_html=content_html)
    connection.close()
    return f"/blog/{slug}"


def _heading(page, name):
    return page.locator(".prose").get_by_role("heading", name=name, exact=True)


def _tall_png(width=800, height=1600):
    """A solid PNG tall enough to push content below it off-screen."""
    def chunk(kind, data):
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(
            ">I", zlib.crc32(body) & 0xFFFFFFFF,
        )

    row = b"\x00" + b"\xcc\xcc\xcc" * width
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(row * height))
        + chunk(b"IEND", b"")
    )


def _wait_heading_below_header(page, heading_id):
    """Wait until #heading_id is on screen with its top below the header."""
    try:
        _wait_for_heading_position(page, heading_id)
    except PlaywrightTimeoutError:
        _capture(page, f"failed-{heading_id}")
        state = page.evaluate(
            """(id) => {
                const el = document.getElementById(id);
                const header = document.getElementById('site-header');
                return {
                    url: location.href,
                    scrollY: window.scrollY,
                    innerHeight: window.innerHeight,
                    headingTop: el ? el.getBoundingClientRect().top : null,
                    headerBottom: header ? header.getBoundingClientRect().bottom : null,
                };
            }""",
            heading_id,
        )
        pytest.fail(f"#{heading_id} not in view below the header: {state}")


def _wait_for_heading_position(page, heading_id):
    page.wait_for_function(
        """(id) => {
            const heading = document.getElementById(id);
            const header = document.getElementById('site-header');
            if (!heading || !header) return false;
            const top = heading.getBoundingClientRect().top;
            const headerBottom = header.getBoundingClientRect().bottom;
            return top >= headerBottom && top < window.innerHeight - 40;
        }""",
        arg=heading_id,
        timeout=SETTLE_TIMEOUT_MS,
    )


def _wait_heading_at_scroll_margin(page, heading_id):
    """Wait until the jump settles with the heading at its scroll margin."""
    page.wait_for_function(
        """(id) => {
            const heading = document.getElementById(id);
            const margin = parseFloat(getComputedStyle(heading).scrollMarginTop);
            return Math.abs(heading.getBoundingClientRect().top - margin) <= 4;
        }""",
        arg=heading_id,
        timeout=SETTLE_TIMEOUT_MS,
    )


def _heading_rect(page, heading_id):
    return page.evaluate(
        """(id) => {
            const rect = document.getElementById(id).getBoundingClientRect();
            const header = document.getElementById('site-header').getBoundingClientRect();
            return {top: rect.top, bottom: rect.bottom, headerBottom: header.bottom};
        }""",
        heading_id,
    )


def _touch_member_context(browser, django_server, email):
    """A signed-in phone context: 390px, touch, no hover (`hover: none`)."""
    context = browser.new_context(
        viewport=PHONE, is_mobile=True, has_touch=True,
    )
    context.add_cookies([{
        "name": "sessionid",
        "value": create_session_for_user(email),
        "domain": "127.0.0.1",
        "path": "/",
    }])
    context.grant_permissions(CLIPBOARD, origin=django_server)
    return context


def _anchor_geometry(page, heading_id):
    """Where the section link sits relative to its heading's own text."""
    return page.evaluate(
        """(id) => {
            const heading = document.getElementById(id);
            const link = heading.querySelector(':scope > a.section-anchor');
            const range = document.createRange();
            range.setStartBefore(heading.firstChild);
            range.setEndBefore(link);
            const lines = [...range.getClientRects()].filter((r) => r.width > 0);
            const last = lines[lines.length - 1];
            const icon = link.querySelector('svg').getBoundingClientRect();
            const hit = link.getBoundingClientRect();
            const box = heading.getBoundingClientRect();
            return {
                linkIsLastChild: heading.lastElementChild === link
                    && heading.lastChild === link,
                textLines: lines.length,
                textRight: last.right,
                lastLineTop: last.top,
                lastLineBottom: last.bottom,
                iconLeft: icon.left,
                iconRight: icon.right,
                iconMiddle: (icon.top + icon.bottom) / 2,
                headingLeft: box.left,
                headingRight: box.right,
                hitWidth: hit.width,
                hitHeight: hit.height,
                opacity: getComputedStyle(link).opacity,
            };
        }""",
        heading_id,
    )


def _assert_link_trails_heading_text(geometry):
    # After the text in DOM order and visually right of the last line, with
    # a small gap; never in the left gutter and never past the heading box.
    assert geometry["linkIsLastChild"], geometry
    assert geometry["iconLeft"] > geometry["headingLeft"], geometry
    gap = geometry["iconLeft"] - geometry["textRight"]
    assert 2 <= gap <= 14, geometry
    assert (
        geometry["lastLineTop"] <= geometry["iconMiddle"] <= geometry["lastLineBottom"]
    ), geometry
    assert geometry["iconRight"] <= geometry["headingRight"] + 1, geometry


def _assert_copied(page, url):
    toast = page.get_by_test_id("section-anchor-toast")
    expect(toast).to_have_attribute("role", "status")
    expect(toast).to_have_attribute("aria-live", "polite")
    expect(toast).to_have_text("Link copied")
    expect(toast).to_be_visible()
    assert page.evaluate("navigator.clipboard.readText()") == url


def _assert_toast_clear_of_section(page, heading_id):
    """Once the jump settles, the toast box (its position does not change
    when it fades) must not cover the linked heading, and must sit above
    the consent banner pinned to the viewport bottom."""
    layout = page.evaluate(
        """(id) => {
            const toast = document.querySelector('[data-testid="section-anchor-toast"]')
                .getBoundingClientRect();
            const heading = document.getElementById(id).getBoundingClientRect();
            const panel = document.getElementById('analytics-consent-panel');
            const panelRect = panel && getComputedStyle(panel).display !== 'none'
                ? panel.getBoundingClientRect() : null;
            return {
                toastTop: toast.top, toastBottom: toast.bottom,
                headingTop: heading.top, headingBottom: heading.bottom,
                panelTop: panelRect ? panelRect.top : null,
                viewport: window.innerHeight,
            };
        }""",
        heading_id,
    )
    assert (
        layout["toastTop"] >= layout["headingBottom"]
        or layout["toastBottom"] <= layout["headingTop"]
    ), layout
    assert layout["toastBottom"] <= layout["viewport"], layout
    if layout["panelTop"] is not None:
        assert layout["toastBottom"] <= layout["panelTop"], layout


def _sign_in_on_login_page(page, email):
    page.fill("#login-email", email)
    page.fill("#login-password", DEFAULT_PASSWORD)
    page.click("#login-submit")


@pytest.mark.core
@browser_journey
def test_member_follows_share_link_to_lesson_section(django_server, browser):
    _reset()
    content_id, lesson_url = _lesson(required_level=20)
    create_user("main-1833@test.com", tier_slug="main")
    context = auth_context(browser, "main-1833@test.com")
    page = context.new_page()
    page.set_viewport_size(DESKTOP)
    try:
        page.goto(
            f"{django_server}/c/{content_id}#streaming-responses",
            wait_until="load",
        )

        assert page.url == f"{django_server}{lesson_url}#streaming-responses"
        _wait_heading_below_header(page, "streaming-responses")
        expect(_heading(page, "Streaming Responses")).to_be_in_viewport()
        setup = _heading_rect(page, "setup")
        assert setup["bottom"] <= setup["headerBottom"], setup
        _capture(page, "share-link-lesson-section")
    finally:
        context.close()


@pytest.mark.core
@browser_journey
def test_reader_opens_direct_article_section_and_follows_body_link(
    django_server, page,
):
    _reset()
    url = _article(
        "anchors-article",
        "## Why it matters\n\nSkip ahead: [read the FAQ](#faq).\n\n"
        + _filler("Why") + "\n\n## How to join\n\n" + _filler("Join")
        + "\n\n## FAQ\n\n" + _filler("FAQ"),
    )
    page.set_viewport_size(DESKTOP)

    page.goto(f"{django_server}{url}#how-to-join", wait_until="load")
    _wait_heading_below_header(page, "how-to-join")

    page.evaluate("window.scrollTo({top: 0, behavior: 'instant'})")
    page.get_by_role("link", name="read the FAQ").click()

    page.wait_for_url(f"{django_server}{url}#faq")
    _wait_heading_below_header(page, "faq")


@browser_journey
def test_author_custom_heading_id_keeps_working(django_server, page):
    _reset()
    url = _article(
        "custom-id-article",
        "## Intro\n\n" + _filler("Intro")
        + "\n\n## Streaming Responses {#streaming}\n\n" + _filler("Stream"),
    )
    page.set_viewport_size(DESKTOP)

    page.goto(f"{django_server}{url}#streaming", wait_until="load")
    _wait_heading_below_header(page, "streaming")

    # A fresh tab, so this is a real page load rather than a same-document
    # fragment change that would keep the previous scroll position.
    fresh = page.context.new_page()
    fresh.set_viewport_size(DESKTOP)
    fresh.goto(f"{django_server}{url}#streaming-responses", wait_until="load")
    expect(fresh.locator("#streaming")).to_have_count(1)
    assert fresh.locator("#streaming-responses").count() == 0
    assert fresh.evaluate("window.scrollY") == 0


@browser_journey
def test_reader_jumps_to_second_identically_named_section(django_server, page):
    from django.db import connection

    from content.models import Workshop, WorkshopPage

    _reset()
    workshop = Workshop.objects.create(
        title="Anchors workshop",
        slug="anchors-workshop",
        date=datetime.date(2026, 9, 1),
        status="published",
        landing_required_level=0,
        pages_required_level=0,
        recording_required_level=0,
    )
    workshop_page = WorkshopPage.objects.create(
        workshop=workshop,
        title="Examples",
        slug="examples",
        sort_order=1,
        body=(
            "## Example\n\n" + _filler("Basic") + "\n\n## Advanced\n\n"
            + _filler("Advanced") + "\n\n## Example\n\n" + _filler("Later")
        ),
    )
    url = workshop_page.get_absolute_url()
    connection.close()
    page.set_viewport_size(DESKTOP)

    page.goto(f"{django_server}{url}#example-1", wait_until="load")
    _wait_heading_below_header(page, "example-1")
    advanced = _heading_rect(page, "advanced")
    assert advanced["top"] < advanced["headerBottom"], advanced

    page.goto(f"{django_server}{url}#example", wait_until="load")
    _wait_heading_below_header(page, "example")


@pytest.mark.core
@browser_journey
def test_visitor_signs_in_from_gated_lesson_and_returns_to_section(
    django_server, page,
):
    _reset()
    ensure_tiers()
    content_id, lesson_url = _lesson(required_level=10)
    create_user("basic-1833@test.com", tier_slug="basic")
    page.set_viewport_size(DESKTOP)

    page.goto(
        f"{django_server}/c/{content_id}#streaming-responses",
        wait_until="load",
    )
    gated_card = page.get_by_test_id("teaser-cta")
    expect(gated_card).to_be_visible()
    assert page.locator("#streaming-responses").count() == 0
    _capture(page, "gated-lesson-before-sign-in")

    # The paid-tier unit teaser card offers Upgrade only; the header's
    # "Sign in" carries ?next=<lesson path> like the card links do.
    page.locator('[data-testid="header-sign-in-link"]:visible').first.click()
    page.wait_for_url("**/accounts/login/**")
    _sign_in_on_login_page(page, "basic-1833@test.com")

    page.wait_for_url(
        f"{django_server}{lesson_url}#streaming-responses",
        timeout=SETTLE_TIMEOUT_MS,
    )
    _wait_heading_below_header(page, "streaming-responses")
    expect(page.locator("#error-handling")).to_have_count(1)
    assert page.evaluate(
        "sessionStorage.getItem('aisl:pending-section')"
    ) is None
    _capture(page, "gated-lesson-after-sign-in")


@browser_journey
def test_saved_section_does_not_hijack_other_pages(django_server, page):
    _reset()
    ensure_tiers()
    content_id, _lesson_url = _lesson(required_level=10)
    article_url = _article(
        "hijack-article",
        "## Intro\n\n" + _filler("Intro")
        + "\n\n## Streaming Responses\n\n" + _filler("Stream"),
    )
    create_user("free-1833@test.com", tier_slug="free")
    page.set_viewport_size(DESKTOP)

    page.goto(
        f"{django_server}/c/{content_id}#streaming-responses",
        wait_until="load",
    )
    assert page.evaluate(
        "JSON.parse(sessionStorage.getItem('aisl:pending-section')).hash"
    ) == "#streaming-responses"

    page.goto(f"{django_server}/accounts/login/", wait_until="domcontentloaded")
    _sign_in_on_login_page(page, "free-1833@test.com")
    page.wait_for_url(f"{django_server}/", timeout=SETTLE_TIMEOUT_MS)
    page.wait_for_load_state("load")
    assert "#" not in page.url
    assert page.evaluate("window.scrollY") == 0

    page.goto(f"{django_server}{article_url}", wait_until="load")
    expect(page.locator("#streaming-responses")).to_have_count(1)
    assert page.url == f"{django_server}{article_url}"
    assert page.evaluate("window.scrollY") == 0


@browser_journey
def test_expired_pending_section_is_ignored(django_server, page):
    _reset()
    ensure_tiers()
    content_id, lesson_url = _lesson(required_level=10)
    create_user("basic-expired-1833@test.com", tier_slug="basic")
    page.set_viewport_size(DESKTOP)

    page.goto(
        f"{django_server}/c/{content_id}#streaming-responses",
        wait_until="load",
    )
    page.evaluate(
        """() => {
            const key = 'aisl:pending-section';
            const entry = JSON.parse(sessionStorage.getItem(key));
            entry.savedAt = Date.now() - 31 * 60 * 1000;
            sessionStorage.setItem(key, JSON.stringify(entry));
        }"""
    )

    page.goto(
        f"{django_server}/accounts/login/?next={lesson_url}",
        wait_until="domcontentloaded",
    )
    _sign_in_on_login_page(page, "basic-expired-1833@test.com")
    page.wait_for_url(f"{django_server}{lesson_url}", timeout=SETTLE_TIMEOUT_MS)
    page.wait_for_load_state("load")

    expect(page.locator("#streaming-responses")).to_have_count(1)
    assert "#" not in page.url
    assert page.evaluate("window.scrollY") == 0
    assert page.evaluate(
        "sessionStorage.getItem('aisl:pending-section')"
    ) is None


@pytest.mark.core
@browser_journey
def test_member_copies_section_link_with_hover_affordance(django_server, browser):
    _reset()
    content_id, lesson_url = _lesson(required_level=20)
    create_user("main-hover-1833@test.com", tier_slug="main")
    context = auth_context(browser, "main-hover-1833@test.com")
    context.grant_permissions(CLIPBOARD, origin=django_server)
    page = context.new_page()
    page.set_viewport_size(DESKTOP)
    try:
        page.goto(f"{django_server}{lesson_url}", wait_until="load")
        heading = _heading(page, "Streaming Responses")
        heading.scroll_into_view_if_needed()
        anchor = heading.locator("a.section-anchor")
        expect(anchor).to_have_attribute(
            "aria-label", "Link to section: Streaming Responses",
        )
        expect(anchor).to_have_attribute("href", "#streaming-responses")
        expect(anchor).to_have_css("opacity", "0")
        expect(page.get_by_test_id("section-anchor-toast")).to_be_hidden()

        # The injected link must not leak into the heading's accessible
        # name: screen readers announce the heading text once, and the link
        # keeps its own label.
        expect(heading).to_have_count(1)
        expect(heading).to_have_accessible_name("Streaming Responses")
        expect(
            page.get_by_role(
                "link", name="Link to section: Streaming Responses", exact=True,
            )
        ).to_have_count(1)

        heading.hover()
        expect(anchor).to_have_css("opacity", "1")
        _assert_link_trails_heading_text(
            _anchor_geometry(page, "streaming-responses"),
        )
        _capture(page, "hover-anchor-visible")

        anchor.click()
        section_url = f"{django_server}{lesson_url}#streaming-responses"
        page.wait_for_url(section_url)
        _assert_copied(page, section_url)
        _capture(page, "desktop-toast")
        _wait_heading_below_header(page, "streaming-responses")
        _wait_heading_at_scroll_margin(page, "streaming-responses")
        _assert_toast_clear_of_section(page, "streaming-responses")
        heading.hover()
        _capture(page, "desktop-link-copied")

        page.reload(wait_until="load")
        _wait_heading_below_header(page, "streaming-responses")
    finally:
        context.close()


@pytest.mark.core
@browser_journey
def test_phone_reader_taps_visible_section_link_and_shares_it(
    django_server, browser,
):
    _reset()
    _, lesson_url = _lesson(required_level=20)
    create_user("main-touch-1833@test.com", tier_slug="main")
    context = _touch_member_context(
        browser, django_server, "main-touch-1833@test.com",
    )
    page = context.new_page()
    try:
        page.goto(f"{django_server}{lesson_url}", wait_until="load")
        assert page.evaluate("matchMedia('(hover: none)').matches")
        heading = _heading(page, "Streaming Responses")
        heading.scroll_into_view_if_needed()
        anchor = page.get_by_role(
            "link", name="Link to section: Streaming Responses", exact=True,
        )
        # No hover on a phone: the link icon is always shown, muted, and is
        # a full 44px tap target that trails the heading text.
        expect(anchor).to_be_visible()
        geometry = _anchor_geometry(page, "streaming-responses")
        assert geometry["opacity"] == "1", geometry
        assert geometry["hitWidth"] >= 44 and geometry["hitHeight"] >= 44, geometry
        _assert_link_trails_heading_text(geometry)
        _capture(page, "touch-anchor-visible")

        anchor.tap()
        section_url = f"{django_server}{lesson_url}#streaming-responses"
        page.wait_for_url(section_url)
        _assert_copied(page, section_url)
        _capture(page, "touch-toast")
        _wait_heading_below_header(page, "streaming-responses")
        _wait_heading_at_scroll_margin(page, "streaming-responses")
        _assert_toast_clear_of_section(page, "streaming-responses")
        _capture(page, "touch-link-copied")

        # The copied link, opened fresh, lands on the section below the
        # fixed header.
        fresh = context.new_page()
        fresh.goto(f"{django_server}{lesson_url}#error-handling", wait_until="load")
        _wait_heading_below_header(fresh, "error-handling")
        _wait_heading_at_scroll_margin(fresh, "error-handling")
        expect(_heading(fresh, "Error Handling")).to_be_in_viewport()
        _capture(fresh, "touch-opened-section")
    finally:
        context.close()


@browser_journey
def test_phone_long_heading_wraps_with_its_section_link(django_server, browser):
    _reset()
    long_title = (
        "Retrieval augmented generation versus agentic retrieval with "
        "tool calling loops"
    )
    url = _article("long-heading", f"## {long_title}\n\nBody text.\n")
    context = browser.new_context(viewport=PHONE, is_mobile=True, has_touch=True)
    page = context.new_page()
    try:
        page.goto(f"{django_server}{url}", wait_until="load")
        heading_id = page.locator(".prose h2[id]").first.get_attribute("id")
        geometry = _anchor_geometry(page, heading_id)
        assert geometry["textLines"] > 1, geometry
        _assert_link_trails_heading_text(geometry)
        assert page.evaluate(
            "document.documentElement.scrollWidth <= window.innerWidth"
        )
        _capture(page, "touch-long-heading")
    finally:
        context.close()


@browser_journey
def test_keyboard_user_reaches_section_link(django_server, page):
    _reset()
    url = _article(
        "keyboard-article",
        "## First section\n\n" + _filler("First")
        + "\n\n## Second section\n\n" + _filler("Second"),
    )
    page.set_viewport_size(DESKTOP)
    page.goto(f"{django_server}{url}", wait_until="load")

    for _ in range(120):
        page.keyboard.press("Tab")
        if page.evaluate(
            "document.activeElement.classList.contains('section-anchor')"
        ):
            break
    else:
        pytest.fail("Tab never reached a section-anchor link")

    focused = page.locator("a.section-anchor:focus")
    expect(focused).to_have_attribute(
        "aria-label", "Link to section: First section",
    )
    expect(focused).to_have_css("opacity", "1")
    assert page.evaluate(
        "getComputedStyle(document.activeElement).boxShadow"
    ) != "none"
    _capture(page, "keyboard-focus-anchor")

    page.keyboard.press("Enter")
    page.wait_for_url(f"{django_server}{url}#first-section")


@browser_journey
def test_hover_link_skips_member_notes_and_keeps_heading_layout(
    django_server, page,
):
    _reset()
    url = _article(
        "layout-article",
        "x",
        content_html=(
            '<h2 id="intro">Intro</h2><p>Body text.</p>'
            '<h3 id="details">Details</h3><p>More.</p>'
            '<div class="prose prose-note"><h2 id="note-heading">Note</h2></div>'
        ),
    )
    page.set_viewport_size(DESKTOP)
    page.goto(f"{django_server}{url}", wait_until="load")

    expect(page.locator("#intro > a.section-anchor")).to_have_count(1)
    expect(page.locator("#details > a.section-anchor")).to_have_count(1)
    expect(page.locator("#note-heading a.section-anchor")).to_have_count(0)

    measure = """() => ['intro', 'details'].map((id) => {
        const el = document.getElementById(id);
        const rect = el.getBoundingClientRect();
        const style = getComputedStyle(el);
        return [rect.x, rect.y, rect.width, rect.height, style.fontSize,
                style.fontWeight, style.color, style.marginTop, style.marginBottom];
    })"""
    with_anchor = page.evaluate(measure)
    page.evaluate(
        """() => {
            document.querySelectorAll('a.section-anchor').forEach((a) => a.remove());
            ['intro', 'details'].forEach((id) => {
                document.getElementById(id).style.position = 'static';
            });
        }"""
    )
    assert page.evaluate(measure) == with_anchor


@browser_journey
def test_section_link_clears_header_with_announcement_banner(django_server, page):
    from django.db import connection

    from content.models import MarketingPage
    from integrations.middleware import clear_announcement_banner_cache
    from integrations.models import AnnouncementBanner

    _reset()
    content_id = uuid.uuid4()
    MarketingPage.objects.create(
        title="Community",
        public_path="/anchors-community",
        content_id=content_id,
        content_markdown=(
            "## About\n\n" + _filler("About") + "\n\n## How to join\n\n"
            + _filler("Join")
        ),
        status="published",
    )
    banner = AnnouncementBanner.get_singleton()
    banner.message = "Cohort 3 starts Monday. Save your seat today."
    banner.link_url = "/events"
    banner.is_enabled = True
    banner.save()
    clear_announcement_banner_cache()
    connection.close()
    page.set_viewport_size({"width": 390, "height": 844})

    page.goto(f"{django_server}/c/{content_id}#how-to-join", wait_until="load")

    expect(page.get_by_test_id("announcement-banner")).to_be_visible()
    _wait_heading_below_header(page, "how-to-join")
    rect = _heading_rect(page, "how-to-join")
    assert rect["top"] >= rect["headerBottom"], rect
    _capture(page, "banner-marketing-section")


@pytest.mark.core
@browser_journey
def test_event_description_section_link(django_server, page):
    from django.db import connection
    from django.utils import timezone

    from events.models import Event

    _reset()
    event = Event.objects.create(
        title="Anchors event",
        slug="anchors-event",
        start_datetime=timezone.now() + datetime.timedelta(days=5),
        status="upcoming",
        description=_filler("Event", paragraphs=10)
        + "\n\n## How to join\n\nOpen the Zoom link.",
    )
    url = event.get_absolute_url()
    connection.close()
    page.set_viewport_size(DESKTOP)

    page.goto(f"{django_server}{url}#how-to-join", wait_until="load")

    _wait_heading_below_header(page, "how-to-join")
    _capture(page, "event-description-section")


@browser_journey
def test_section_link_stays_on_target_while_lazy_image_above_loads(
    django_server, page,
):
    held = []
    url = _article(
        "lazy-image-article",
        "x",
        content_html=(
            '<h2 id="intro">Intro</h2>'
            + "".join(f"<p>Intro paragraph {n}. " + "Words keep going. " * 30 + "</p>"
                      for n in range(10))
            + '<h2 id="phase-3">Phase 3</h2>'
            + "".join(f"<p>Phase paragraph {n}. " + "Words keep going. " * 30 + "</p>"
                      for n in range(6))
            + '<p><img src="/static/anchors-tall-1833.png" alt="Tall diagram" '
            'loading="lazy"></p>'
            + '<h2 id="phase-4-modeling">Phase 4: Modeling</h2>'
            + "".join(f"<p>Model paragraph {n}. " + "Words keep going. " * 30 + "</p>"
                      for n in range(10))
        ),
    )
    page.set_viewport_size(DESKTOP)
    page.route("**/anchors-tall-1833.png", lambda route: held.append(route))

    page.goto(f"{django_server}{url}#phase-4-modeling", wait_until="domcontentloaded")
    _wait_heading_below_header(page, "phase-4-modeling")
    for _ in range(50):
        if held:
            break
        page.wait_for_timeout(100)
    assert held, "the lazy image above the target was never requested"

    # The image arrives after the fragment scroll: 1600px of content is
    # inserted above the heading.
    held[0].fulfill(status=200, content_type="image/png", body=_tall_png())
    page.wait_for_function(
        "() => document.querySelector('img[alt=\"Tall diagram\"]').naturalHeight > 0"
    )

    _wait_heading_below_header(page, "phase-4-modeling")
    rect = _heading_rect(page, "phase-4-modeling")
    assert rect["top"] - rect["headerBottom"] < 60, rect
    _capture(page, "lazy-image-target-kept")
