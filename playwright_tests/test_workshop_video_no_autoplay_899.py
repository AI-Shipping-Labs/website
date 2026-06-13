"""Playwright E2E tests for issue #899: the "Watch this section" / ?t=
deep link cues the recording at the timestamp WITHOUT auto-playing.

Decision (Option 1): same-tab navigation to ``/video``, cued and paused.

What each layer asserts:

- Self-hosted ``<video>``: this is the only source we can fully assert
  from Playwright because it is same-origin. We use a committed tiny
  (~16 KB, 1010 s) fixture mp4 served from ``/static`` so the browser
  can actually load metadata and seek. We assert the LIVE media API
  (``currentTime``, ``paused``) via ``evaluate`` — never HTML string
  matching for playback state, per _docs/testing-guidelines.md.
- YouTube / Loom (cross-origin iframes): we cannot read their internal
  playing state from Playwright, so we assert the rendered cue contract
  (``start: 960`` playerVar / ``t=960`` in the Loom src) AND the absence
  of any autoplay request. The full "is it actually playing" check is
  the [HUMAN] acceptance criterion on the issue.

Usage:
    uv run pytest playwright_tests/test_workshop_video_no_autoplay_899.py -v
"""

import datetime
import os

import pytest

from playwright_tests.conftest import (
    SETTLE_TIMEOUT_MS,
)
from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_user as _create_user,
)

os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')
from django.db import connection  # noqa: E402

# Local-only: DB seeding + session-cookie auth + a same-origin static
# fixture served by the in-process runserver. Cannot run against a
# deployed environment. See _docs/testing-guidelines.md.
pytestmark = pytest.mark.local_only


def _clear_workshops():
    from content.models import Workshop, WorkshopPage
    from events.models import Event
    WorkshopPage.objects.all().delete()
    Workshop.objects.all().delete()
    Event.objects.all().delete()
    connection.close()


def _create_workshop(recording_url):
    """Create a workshop whose recording is ``recording_url``.

    Page C has ``video_start='16:00'`` (== 960 s) and the recording has
    a 16:00 chapter, so the watch bar links to ``/video?t=16:00`` and
    the chapter list offers a 16:00 row to click.
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
        slug='ws-event-899',
        title='WS 899',
        start_datetime=timezone.now(),
        status='completed',
        kind='workshop',
        recording_url=recording_url,
        timestamps=[
            {'time': '0:00', 'title': 'Welcome'},
            {'time': '16:00', 'title': 'Setup chapter'},
        ],
        materials=[],
        published=True,
    )
    workshop = Workshop.objects.create(
        slug='ws899',
        title='No Autoplay Workshop',
        date=datetime.date(2026, 4, 21),
        status='published',
        landing_required_level=0,
        pages_required_level=10,
        recording_required_level=20,
        description='Workshop body.',
        event=event,
    )
    instructor, _ = Instructor.objects.get_or_create(
        instructor_id=slugify('Alexey')[:200] or 'test-instructor',
        defaults={'name': 'Alexey', 'status': 'published'},
    )
    WorkshopInstructor.objects.get_or_create(
        workshop=workshop, instructor=instructor,
        defaults={'position': 0},
    )
    WorkshopPage.objects.create(
        workshop=workshop, slug='page-c', title='Page C',
        sort_order=1, body='Page C body', video_start='16:00',
    )
    connection.close()
    return workshop


def _self_hosted_url(django_server):
    # Tiny committed fixture; ends in .mp4 so detect_video_source()
    # classifies it as self_hosted, and is long enough (1010 s) that
    # seeking to 960 s is valid (not clamped to the end).
    return f'{django_server}/static/test_media/cue_fixture.mp4'


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestSelfHostedCuePausedThenPlays:
    """Self-hosted recording: cued at the offset, paused, resumes on play."""

    def test_watch_bar_lands_cued_and_paused_then_plays_from_offset(
        self, browser, django_server,
    ):
        _clear_workshops()
        workshop = _create_workshop(_self_hosted_url(django_server))
        _create_user('main@test.com', tier_slug='main')
        url_key = workshop.url_key

        ctx = _auth_context(browser, 'main@test.com')
        page = ctx.new_page()

        # Reader is on the section page; click "Watch this section (16:00)".
        page.goto(
            f'{django_server}/workshops/{url_key}/tutorial/page-c',
            wait_until='domcontentloaded',
        )
        bar = page.locator('[data-testid="watch-this-section"]')
        assert bar.count() == 1

        # No new tab/window: clicking navigates the same page.
        before_pages = len(ctx.pages)
        bar.click()
        page.wait_for_load_state('domcontentloaded')
        assert len(ctx.pages) == before_pages, 'watch bar must not open a new tab'
        assert page.url.endswith(f'/workshops/{url_key}/video?t=16:00')

        video = page.locator('#video-player-self-hosted')
        assert video.count() == 1

        # The local test static server does not honor HTTP Range requests
        # (production self-hosted recordings live on S3/CloudFront, which
        # do), so the source isn't seekable until fully buffered. Nudge a
        # full buffer to simulate a range-capable source; the page's OWN
        # `canplay` cue handler — not the test — then seeks to the offset.
        page.evaluate(
            "() => { const v = document.getElementById('video-player-self-hosted');"
            " v.preload = 'auto'; v.load(); }"
        )
        # The full-buffer + canplay + seek round-trip is buffering-bound and
        # runs slower under CI load than locally, so allow a generous budget
        # before asserting the cued playhead. This is purely a timing budget;
        # the assertion below still fails loudly if the cue handler is broken.
        page.wait_for_function(
            "() => { const v = document.getElementById('video-player-self-hosted');"
            " return v && v.currentTime > 900; }",
            timeout=45000,
        )

        current_time = page.evaluate(
            "() => document.getElementById('video-player-self-hosted').currentTime"
        )
        assert 955 <= current_time <= 965, (
            f'expected playhead cued at ~960s, got {current_time}'
        )

        # The player is PARKED, not playing.
        paused = page.evaluate(
            "() => document.getElementById('video-player-self-hosted').paused"
        )
        assert paused is True, 'player must be paused on arrival (no autoplay)'

        # Pressing play resumes from the cued offset, not from 0.
        page.evaluate(
            "() => document.getElementById('video-player-self-hosted').play()"
        )
        page.wait_for_function(
            "() => document.getElementById('video-player-self-hosted').paused === false",
            timeout=SETTLE_TIMEOUT_MS,
        )
        playing_time = page.evaluate(
            "() => document.getElementById('video-player-self-hosted').currentTime"
        )
        assert playing_time >= 955, (
            f'play must resume from the cued offset, got {playing_time}'
        )

        ctx.close()

    def test_bare_video_page_parks_at_zero_paused(
        self, browser, django_server,
    ):
        # No ?t= -> the page must never auto-play; the player sits at 0.
        _clear_workshops()
        workshop = _create_workshop(_self_hosted_url(django_server))
        _create_user('main@test.com', tier_slug='main')
        url_key = workshop.url_key

        ctx = _auth_context(browser, 'main@test.com')
        page = ctx.new_page()
        page.goto(
            f'{django_server}/workshops/{url_key}/video',
            wait_until='domcontentloaded',
        )
        video = page.locator('#video-player-self-hosted')
        assert video.count() == 1
        page.wait_for_function(
            "() => { const v = document.getElementById('video-player-self-hosted');"
            " return v && v.readyState >= 1; }",
            timeout=15000,
        )
        # Give any (buggy) autoplay a moment to start.
        page.wait_for_timeout(500)
        state = page.evaluate(
            "() => { const v = document.getElementById('video-player-self-hosted');"
            " return { paused: v.paused, currentTime: v.currentTime }; }"
        )
        assert state['paused'] is True
        assert state['currentTime'] < 1, (
            f'bare page must park at 0, got {state["currentTime"]}'
        )

        ctx.close()


@pytest.mark.django_db(transaction=True)
class TestSelfHostedChapterClickStillPlays:
    """Boundary: explicit chapter clicks keep their seek-AND-play behavior."""

    def test_chapter_click_seeks_and_plays(self, browser, django_server):
        _clear_workshops()
        workshop = _create_workshop(_self_hosted_url(django_server))
        _create_user('main@test.com', tier_slug='main')
        url_key = workshop.url_key

        ctx = _auth_context(browser, 'main@test.com')
        page = ctx.new_page()
        # Bare page (no ?t=) so we start paused at 0 before clicking.
        page.goto(
            f'{django_server}/workshops/{url_key}/video',
            wait_until='domcontentloaded',
        )
        # Force a full buffer so the local (non-range) static source is
        # seekable, the same way a range-capable CDN source would be.
        page.evaluate(
            "() => { const v = document.getElementById('video-player-self-hosted');"
            " v.preload = 'auto'; v.load(); }"
        )
        page.wait_for_function(
            "() => { const v = document.getElementById('video-player-self-hosted');"
            " return v && v.seekable.length && v.seekable.end(0) > 960; }",
            timeout=15000,
        )
        # Confirm we start paused at 0 (no autoplay, no ?t= cue here).
        assert page.evaluate(
            "() => document.getElementById('video-player-self-hosted').paused"
        ) is True
        assert page.evaluate(
            "() => document.getElementById('video-player-self-hosted').currentTime"
        ) < 1

        # Expand the Chapters accordion and click the 16:00 (960s) row.
        page.evaluate(
            "document.querySelectorAll('details[data-testid=\"video-chapters\"]')"
            ".forEach(d => d.open = true)"
        )
        ts_btn = page.locator('.video-timestamp[data-time-seconds="960"]')
        assert ts_btn.count() == 1
        ts_btn.click()

        # Explicit chapter click seeks AND plays.
        page.wait_for_function(
            "() => { const v = document.getElementById('video-player-self-hosted');"
            " return v.currentTime > 900 && v.paused === false; }",
            timeout=SETTLE_TIMEOUT_MS,
        )
        state = page.evaluate(
            "() => { const v = document.getElementById('video-player-self-hosted');"
            " return { paused: v.paused, currentTime: v.currentTime }; }"
        )
        assert state['paused'] is False
        assert state['currentTime'] >= 955

        ctx.close()


@pytest.mark.django_db(transaction=True)
class TestYouTubeCuedNotAutoplaying:
    """YouTube: rendered config requests start=960 and never autoplay."""

    def test_youtube_deep_link_cues_without_autoplay(
        self, browser, django_server,
    ):
        _clear_workshops()
        workshop = _create_workshop(
            'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
        )
        _create_user('main@test.com', tier_slug='main')
        url_key = workshop.url_key

        ctx = _auth_context(browser, 'main@test.com')
        page = ctx.new_page()
        page.goto(
            f'{django_server}/workshops/{url_key}/video?t=16:00',
            wait_until='domcontentloaded',
        )
        # The YT player container renders.
        assert page.locator('#yt-player-dQw4w9WgXcQ').count() == 1
        # Inspect the YT IFrame API init script: it must request the
        # offset via playerVars.start and must NOT request autoplay or
        # call playVideo() on load. (We scope to the init script, not the
        # whole page — the page's video iframes carry an allow="autoplay"
        # permission attribute, which permits but does not force playback.)
        init_script = page.evaluate(
            "() => Array.from(document.scripts)"
            ".map(s => s.textContent)"
            ".find(t => t && t.includes('YT.Player')) || ''"
        )
        assert 'start: 960' in init_script
        assert 'autoplay' not in init_script
        assert 'playVideo()' not in init_script

        ctx.close()


@pytest.mark.django_db(transaction=True)
class TestLoomCuedNotAutoplaying:
    """Loom: iframe src carries t=960 and no autoplay=1."""

    def test_loom_deep_link_cues_without_autoplay(
        self, browser, django_server,
    ):
        _clear_workshops()
        workshop = _create_workshop(
            'https://www.loom.com/share/abc123def456',
        )
        _create_user('main@test.com', tier_slug='main')
        url_key = workshop.url_key

        ctx = _auth_context(browser, 'main@test.com')
        page = ctx.new_page()
        page.goto(
            f'{django_server}/workshops/{url_key}/video?t=16:00',
            wait_until='domcontentloaded',
        )
        iframe = page.locator('#loom-player-abc123def456')
        assert iframe.count() == 1
        src = iframe.get_attribute('src')
        assert 't=960' in src, f'Loom src must cue to 960s, got {src}'
        assert 'autoplay=1' not in src, (
            f'Loom src must not request autoplay, got {src}'
        )

        ctx.close()


@pytest.mark.django_db(transaction=True)
class TestGatedReaderCannotDeepLinkPastPaywall:
    """A Basic reader sees no watch bar and hits the paywall on /video."""

    def test_basic_user_paywalled_no_player_no_cue(
        self, browser, django_server,
    ):
        _clear_workshops()
        workshop = _create_workshop(_self_hosted_url(django_server))
        _create_user('basic@test.com', tier_slug='basic')
        url_key = workshop.url_key

        ctx = _auth_context(browser, 'basic@test.com')
        page = ctx.new_page()

        # Reading page: body accessible (Basic passes the pages gate) but
        # no watch bar (Basic is below the recording gate).
        page.goto(
            f'{django_server}/workshops/{url_key}/tutorial/page-c',
            wait_until='domcontentloaded',
        )
        assert page.locator('[data-testid="watch-this-section"]').count() == 0

        # Direct deep link: paywall renders, no player, no cue parsing.
        page.goto(
            f'{django_server}/workshops/{url_key}/video?t=16:00',
            wait_until='domcontentloaded',
        )
        assert page.locator('[data-testid="video-paywall"]').count() == 1
        assert page.locator('#video-player-self-hosted').count() == 0
        body = page.content()
        assert 'start: 960' not in body
        assert 'var startSeconds' not in body

        ctx.close()
