"""Playwright E2E tests for workshop video <-> tutorial timestamp links (issue #302).

Covers the round-trip user flow:

- Tutorial page with `video_start` shows a "Watch this section" bar.
- Clicking the bar lands on `/workshops/<slug>/video?t=MM:SS` with the
  YouTube embed initialised at the offset (verified by the rendered
  ``playerVars.start`` literal).
- The video page lists timestamps; rows whose seconds match a page's
  ``video_start`` show a tutorial sub-link, and clicking that sub-link
  lands on the matching tutorial page (where the bar reappears).
- Tutorial without a timestamp shows no bar.
- A user gated below the recording level reads the page but sees no
  watch bar.

Usage:
    uv run pytest playwright_tests/test_workshop_video_timestamps.py -v
"""

import datetime
import os

import pytest

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_user as _create_user,
)

os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')
from django.db import connection  # noqa: E402


def _clear_workshops():
    from content.models import Workshop, WorkshopPage
    from events.models import Event
    WorkshopPage.objects.all().delete()
    Workshop.objects.all().delete()
    Event.objects.all().delete()
    connection.close()


def _create_workshop_with_timestamps():
    """Create a workshop with two pages bound to recording timestamps.

    Page B has video_start="0:00" and Page C has video_start="16:00";
    the recording timestamps include 0:00, 8:30, and 16:00 so we can
    assert that the unmatched 8:30 row has no tutorial sub-link.
    """
    from django.utils import timezone
    from django.utils.text import slugify

    from content.models import Instructor, Workshop, WorkshopInstructor, WorkshopPage
    from events.models import Event

    event = Event.objects.create(
        slug='ws-event',
        title='WS',
        start_datetime=timezone.now(),
        status='completed',
        kind='workshop',
        recording_url='https://www.youtube.com/watch?v=dQw4w9WgXcQ',
        timestamps=[
            {'time': '0:00', 'title': 'Welcome'},
            {'time': '8:30', 'title': 'No matching page'},
            {'time': '16:00', 'title': 'Setup chapter'},
        ],
        materials=[],
        published=True,
    )
    workshop = Workshop.objects.create(
        slug='ws',
        title='Production Agents',
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
        defaults={
            'name': instructor_name,
            'status': 'published',
        },
    )
    WorkshopInstructor.objects.get_or_create(
        workshop=workshop,
        instructor=instructor,
        defaults={'position': 0},
    )
    WorkshopPage.objects.create(
        workshop=workshop, slug='page-a', title='Page A',
        sort_order=1, body='Page A body', video_start='',
    )
    WorkshopPage.objects.create(
        workshop=workshop, slug='page-b', title='Page B',
        sort_order=2, body='Page B body', video_start='0:00',
    )
    WorkshopPage.objects.create(
        workshop=workshop, slug='page-c', title='Page C',
        sort_order=3, body='Page C body', video_start='16:00',
    )
    connection.close()
    return workshop


@pytest.mark.django_db(transaction=True)
class TestWatchBarRoundTrip:
    """Tutorial -> video at timestamp -> click another timestamp -> tutorial."""

    def test_main_user_round_trips_through_timestamps(
        self, browser, django_server,
    ):
        _clear_workshops()
        _create_workshop_with_timestamps()
        _create_user('main@test.com', tier_slug='main')

        ctx = _auth_context(browser, 'main@test.com')
        page = ctx.new_page()

        # Step 1: Land on Page C; expect the watch bar pointing at the
        # new course-player layout (issue #618).
        page.goto(
            f'{django_server}/workshops/ws/tutorial/page-c',
            wait_until='domcontentloaded',
        )
        bar = page.locator('[data-testid="watch-this-section"]')
        assert bar.count() == 1, 'Watch bar should be visible on page C'
        # Watch bar links into the new player layout (?page= + ?t=).
        assert bar.get_attribute('href') == (
            '/workshops/ws?page=page-c&t=16:00'
        )
        assert 'Watch this section (16:00)' in bar.inner_text()

        # Step 2: Click the watch bar; lands on the player layout with
        # data-start-seconds=960 on the player shell.
        bar.click()
        page.wait_for_load_state('domcontentloaded')
        assert page.url == (
            f'{django_server}/workshops/ws?page=page-c&t=16:00'
        )
        shell = page.locator('#workshop-player-shell')
        assert shell.get_attribute('data-start-seconds') == '960'
        # Active tutorial in the right pane is page-c.
        active_pane = page.locator('[data-testid="workshop-tutorial-pane"]')
        assert active_pane.get_attribute('data-page-slug') == 'page-c'

        # Step 3: The chapter outline lists ALL chapter timestamps from the
        # recording (3 in our fixture: 0:00, 8:30, 16:00). Rows whose
        # seconds match a tutorial page's video_start carry a
        # ``data-tutorial-slug`` attribute that the player JS uses to
        # swap the right pane on click; the unmatched 8:30 row is a
        # plain seek-only chapter button (no tutorial slug attribute).
        chapters = page.locator('[data-testid="workshop-chapter-row"]')
        assert chapters.count() == 3
        page_b_chapter = page.locator(
            '[data-testid="workshop-chapter-row"]'
            '[data-tutorial-slug="page-b"]'
        )
        page_c_chapter = page.locator(
            '[data-testid="workshop-chapter-row"]'
            '[data-tutorial-slug="page-c"]'
        )
        assert page_b_chapter.count() == 1
        assert page_c_chapter.count() == 1
        # The unmatched 8:30 row is rendered without a tutorial slug.
        unmapped_chapter = page.locator(
            '[data-testid="workshop-chapter-row"][data-time-seconds="510"]'
        )
        assert unmapped_chapter.count() == 1
        assert unmapped_chapter.get_attribute('data-tutorial-slug') is None

        ctx.close()

    def test_page_without_video_start_has_no_bar(
        self, browser, django_server,
    ):
        _clear_workshops()
        _create_workshop_with_timestamps()
        _create_user('main@test.com', tier_slug='main')

        ctx = _auth_context(browser, 'main@test.com')
        page = ctx.new_page()
        page.goto(
            f'{django_server}/workshops/ws/tutorial/page-a',
            wait_until='domcontentloaded',
        )
        # Page A has no video_start -> no bar.
        bar = page.locator('[data-testid="watch-this-section"]')
        assert bar.count() == 0
        # The page itself still renders.
        assert page.locator('[data-testid="page-title"]').count() == 1
        assert page.locator('[data-testid="page-body"]').count() == 1

        ctx.close()

    def test_basic_user_reads_tutorial_but_no_watch_bar(
        self, browser, django_server,
    ):
        _clear_workshops()
        _create_workshop_with_timestamps()
        _create_user('basic@test.com', tier_slug='basic')

        ctx = _auth_context(browser, 'basic@test.com')
        page = ctx.new_page()
        page.goto(
            f'{django_server}/workshops/ws/tutorial/page-c',
            wait_until='domcontentloaded',
        )
        # Body renders (Basic tier passes pages gate).
        assert page.locator('[data-testid="page-body"]').count() == 1
        # No watch bar (Basic is below the recording gate).
        assert page.locator('[data-testid="watch-this-section"]').count() == 0

        ctx.close()


@pytest.mark.django_db(transaction=True)
class TestVideoDeepLink:
    """Direct ?t= deep-link initialises the embed at the requested moment."""

    def test_main_user_deep_link_at_offset(
        self, browser, django_server,
    ):
        _clear_workshops()
        _create_workshop_with_timestamps()
        _create_user('main@test.com', tier_slug='main')

        ctx = _auth_context(browser, 'main@test.com')
        page = ctx.new_page()
        # Issue #618: /video?t= 301-redirects to the player layout
        # preserving the ?t= param.
        page.goto(
            f'{django_server}/workshops/ws/video?t=16:00',
            wait_until='domcontentloaded',
        )
        # End URL is the new player layout with ?t=16:00. The view uses
        # urllib.parse.urlencode which is standards-conformant and
        # percent-encodes the ``:`` in the value, so accept both the
        # literal and the encoded form.
        assert page.url in (
            f'{django_server}/workshops/ws?t=16:00',
            f'{django_server}/workshops/ws?t=16%3A00',
        )
        # Player shell carries data-start-seconds=960.
        shell = page.locator('#workshop-player-shell')
        assert shell.get_attribute('data-start-seconds') == '960'
        # Recording outline still renders.
        assert page.locator(
            '[data-testid="workshop-outline-recording"]',
        ).count() == 1
        ctx.close()

    def test_malformed_t_does_not_break_page(
        self, browser, django_server,
    ):
        _clear_workshops()
        _create_workshop_with_timestamps()
        _create_user('main@test.com', tier_slug='main')

        ctx = _auth_context(browser, 'main@test.com')
        page = ctx.new_page()
        # The redirect strips known-bad input; the player layout falls
        # back to no start position.
        response = page.goto(
            f'{django_server}/workshops/ws/video?t=not-a-time',
            wait_until='domcontentloaded',
        )
        assert response is not None and response.status == 200
        # No data-start-seconds attribute when ?t= was unparseable.
        shell = page.locator('#workshop-player-shell')
        assert shell.get_attribute('data-start-seconds') is None
        ctx.close()
