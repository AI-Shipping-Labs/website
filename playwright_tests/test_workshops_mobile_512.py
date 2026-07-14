"""Mobile coverage for the canonical past-recordings archive.

Issue #512 originally protected a homepage recordings carousel. Issue #1241
deliberately removed that borrowed-proof section from home; recordings now
remain discoverable at ``/events?filter=past``. These scenarios preserve the
original narrow-viewport, long-title, card-reachability, responsive, and empty
state guarantees on the canonical archive while also proving home stays clean.
"""

import datetime
import os
from pathlib import Path

import pytest
from django.db import connection
from django.utils import timezone
from playwright.sync_api import expect

from playwright_tests.conftest import ensure_site_config_tiers

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = pytest.mark.local_only

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
    """Create published completed events shown by the past-recordings filter."""
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


def _open_archive(page, django_server):
    page.goto(
        f"{django_server}/events?filter=past",
        wait_until="networkidle",
    )
    expect(page.get_by_test_id("events-filter-past")).to_have_attribute(
        "aria-selected", "true",
    )


@pytest.mark.django_db(transaction=True)
def test_pixel7_archive_is_overflow_free_and_home_omits_recordings(
    django_server, page,
):
    ensure_site_config_tiers()
    _clear_recordings()
    _seed_recordings(n=4)

    page.set_viewport_size(PIXEL_7)
    page.goto(f"{django_server}/", wait_until="networkidle")
    expect(page.locator("#resources")).to_have_count(0)
    expect(page.get_by_test_id("home-recordings-carousel")).to_have_count(0)

    _open_archive(page, django_server)
    expect(page.get_by_test_id("past-recording-card")).to_have_count(4)
    assert _doc_overflow(page) <= 1
    _section_screenshot(
        page, '[data-testid="past-recordings-stack"]', "393x851-default",
    )


@pytest.mark.django_db(transaction=True)
def test_long_title_wraps_inside_archive_card_on_pixel7(django_server, page):
    _clear_recordings()
    _seed_recordings(n=3, long_title_index=0)

    page.set_viewport_size(PIXEL_7)
    _open_archive(page, django_server)
    assert _doc_overflow(page) <= 1

    card = page.get_by_test_id("past-recording-card").filter(
        has_text=LONG_TOKEN_TITLE,
    )
    expect(card).to_have_count(1)
    geom = card.evaluate(
        """card => {
          const heading = card.querySelector('h3');
          const rect = card.getBoundingClientRect();
          return {
            left: rect.left,
            right: rect.right,
            headingScroll: heading.scrollWidth,
            headingClient: heading.clientWidth,
            headingHeight: heading.getBoundingClientRect().height,
          };
        }"""
    )
    assert geom["left"] >= -1
    assert geom["right"] <= PIXEL_7["width"] + 1
    assert geom["headingScroll"] - geom["headingClient"] <= 1
    assert geom["headingHeight"] > 40
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    card.screenshot(path=SCREENSHOT_DIR / "393x851-longtitle.png")


@pytest.mark.django_db(transaction=True)
def test_mobile_archive_uses_reachable_vertical_cards(django_server, page):
    _clear_recordings()
    _seed_recordings(n=4)

    page.set_viewport_size(PIXEL_7)
    _open_archive(page, django_server)
    stack = page.get_by_test_id("past-recordings-stack")
    expect(stack).to_be_visible()
    assert stack.evaluate("el => el.scrollWidth - el.clientWidth") <= 1
    cards = stack.get_by_test_id("past-recording-card")
    expect(cards).to_have_count(4)
    for index in range(4):
        card = cards.nth(index)
        card.scroll_into_view_if_needed()
        expect(card).to_be_visible()
        expect(card.get_by_test_id("past-card-event-link")).to_be_visible()
    assert _doc_overflow(page) <= 1


@pytest.mark.django_db(transaction=True)
def test_iphone_se_no_horizontal_overflow(django_server, page):
    _clear_recordings()
    _seed_recordings(n=4)

    page.set_viewport_size(IPHONE_SE)
    _open_archive(page, django_server)
    assert _doc_overflow(page) <= 1
    _screenshot(page, "375x812")


@pytest.mark.django_db(transaction=True)
def test_legacy_320_no_horizontal_overflow(django_server, page):
    _clear_recordings()
    _seed_recordings(n=4)

    page.set_viewport_size(LEGACY_320)
    _open_archive(page, django_server)
    assert _doc_overflow(page) <= 1
    _screenshot(page, "320x568")


@pytest.mark.django_db(transaction=True)
def test_tablet_archive_keeps_all_recordings_reachable(django_server, page):
    _clear_recordings()
    _seed_recordings(n=3)

    page.set_viewport_size(TABLET_1024)
    _open_archive(page, django_server)
    stack = page.get_by_test_id("past-recordings-stack")
    expect(stack.get_by_test_id("past-recording-card")).to_have_count(3)
    assert stack.evaluate("el => el.scrollWidth - el.clientWidth") <= 1
    assert _doc_overflow(page) <= 1
    _screenshot(page, "desktop-1024")


@pytest.mark.django_db(transaction=True)
def test_laptop_archive_preserves_clickable_card_chrome(django_server, page):
    _clear_recordings()
    _seed_recordings(n=3)

    page.set_viewport_size(LAPTOP_1280)
    _open_archive(page, django_server)
    cards = page.get_by_test_id("past-recording-card")
    expect(cards).to_have_count(3)
    info = cards.first.evaluate(
        """card => {
          const link = card.querySelector('[data-testid="past-card-event-link"]');
          const cardStyle = getComputedStyle(card);
          const linkStyle = getComputedStyle(link);
          return {
            border: cardStyle.borderTopWidth,
            padding: cardStyle.paddingTop,
            linkDisplay: linkStyle.display,
          };
        }"""
    )
    assert info == {
        "border": "1px",
        "padding": "20px",
        "linkDisplay": "block",
    }
    _screenshot(page, "desktop-1280")


@pytest.mark.django_db(transaction=True)
def test_empty_archive_state_no_overflow_on_pixel7(django_server, page):
    ensure_site_config_tiers()
    _clear_recordings()

    page.set_viewport_size(PIXEL_7)
    page.goto(f"{django_server}/", wait_until="networkidle")
    expect(page.locator("#resources")).to_have_count(0)
    _open_archive(page, django_server)
    expect(
        page.get_by_text("No past event recordings yet", exact=True)
    ).to_be_visible()
    expect(page.get_by_test_id("past-recordings-stack")).to_have_count(0)
    assert _doc_overflow(page) <= 1
