"""Playwright coverage for the shared player's chapter timeline."""

import datetime
import os
from pathlib import Path

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import auth_context, create_user
from scripts.browser_journey_policy import browser_journey
from tests.test_video_fixture_range_response import fixture_range_response

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection

pytestmark = pytest.mark.local_only
FIXTURE_PATH = (
    Path(__file__).resolve().parents[1]
    / "static"
    / "test_media"
    / "cue_fixture.mp4"
)


def _clear_workshops():
    from content.models import Workshop
    from events.models import Event

    Workshop.objects.all().delete()
    Event.objects.all().delete()
    connection.close()


def _create_workshop(recording_url, recording_level, timestamps):
    from django.utils import timezone

    from content.models import Workshop
    from events.models import Event

    start = timezone.now() - datetime.timedelta(days=1)
    event = Event.objects.create(
        title="Chapter timeline workshop", slug="chapter-timeline",
        start_datetime=start, status="completed", kind="workshop",
        required_level=recording_level, recording_url=recording_url,
        timestamps=timestamps, materials=[], published=True,
    )
    pages_level = 0
    if recording_level > 0:
        pages_level = 10
    return Workshop.objects.create(
        slug="chapter-timeline", title="Chapter timeline workshop",
        date=start.date(), status="published", landing_required_level=0,
        pages_required_level=pages_level,
        recording_required_level=recording_level,
        description="Workshop body.", event=event,
    )


def _serve_fixture_with_ranges(page, django_server):
    fixture = FIXTURE_PATH.read_bytes()
    url = f"{django_server}/static/test_media/cue_fixture.mp4"

    def fulfill(route):
        range_header = route.request.headers.get("range", "")
        route.fulfill(**fixture_range_response(fixture, range_header))

    page.route(url, fulfill)


def _open_workshop_video(browser, django_server, workshop, email=None):
    if email:
        context = auth_context(browser, email)
    else:
        context = browser.new_context()
    page = context.new_page()
    if workshop.event.recording_url.endswith(".mp4"):
        _serve_fixture_with_ranges(page, django_server)
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(
        f"{django_server}/workshops/{workshop.url_key}/video",
        wait_until="domcontentloaded",
    )
    return context, page


def _install_youtube_player_stub(page):
    page.add_init_script(
        "window.__chapterClock = {time: 0, state: 1};"
        "window.YT = {PlayerState: {PLAYING: 1}, Player: function(hostId) {"
        "var clock = window.__chapterClock;"
        "return {getIframe: function() {return document.getElementById(hostId);},"
        "seekTo: function(time) {clock.time = time;},"
        "getCurrentTime: function() {return clock.time;},"
        "getPlayerState: function() {return clock.state;}};}};"
    )
    page.route(
        "https://www.youtube.com/embed/**",
        lambda route: route.fulfill(status=200, content_type="text/html", body=""),
    )


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_main_member_uses_keyboard_chapters_and_tracks_self_hosted_playback(
    browser, django_server,
):
    _clear_workshops()
    user = create_user("chapters-main@test.com", tier_slug="main")
    workshop = _create_workshop(
        f"{django_server}/static/test_media/cue_fixture.mp4", 20,
        [{"time_seconds": 0, "label": "Welcome"},
         {"time_seconds": 150, "label": "Main section"},
         {"time_seconds": 500, "label": "Wrap up"}],
    )
    context, page = _open_workshop_video(
        browser, django_server, workshop, email=user.email,
    )
    video = page.locator("video")
    page.wait_for_function("document.querySelector('video').readyState >= 1")
    markers = page.get_by_test_id("video-chapter-timeline").get_by_role("button")
    expect(markers).to_have_count(3)
    markers.nth(1).focus()
    markers.nth(1).press("Enter")
    expect(markers.nth(1)).to_have_attribute("aria-current", "location")
    page.wait_for_function("document.querySelector('video').currentTime >= 149.9")
    video.evaluate("node => { node.currentTime = 550; node.dispatchEvent(new Event('timeupdate')); }")
    expect(markers.nth(2)).to_have_attribute("aria-current", "location")
    expect(page.locator("html")).to_have_js_property("scrollWidth", 390)
    context.close()


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_youtube_playback_updates_the_active_chapter(browser, django_server):
    _clear_workshops()
    page = browser.new_page()
    _install_youtube_player_stub(page)
    workshop = _create_workshop(
        "https://www.youtube.com/watch?v=chapter123", 0,
        [{"time_seconds": 0, "label": "Welcome"},
         {"time_seconds": 150, "label": "Main section"},
         {"time_seconds": 500, "label": "Wrap up"}],
    )
    page.goto(
        f"{django_server}/workshops/{workshop.url_key}/video",
        wait_until="domcontentloaded",
    )
    markers = page.get_by_test_id("video-chapter-timeline").get_by_role("button")
    expect(page.get_by_title("YouTube video player", exact=True)).to_be_visible()
    markers.nth(1).click()
    assert page.evaluate("window.__chapterClock.time") == 150
    expect(markers.nth(1)).to_have_attribute("aria-current", "location")
    page.evaluate("window.__chapterClock.time = 550")
    expect(markers.nth(2)).to_have_attribute("aria-current", "location")
    page.context.close()
