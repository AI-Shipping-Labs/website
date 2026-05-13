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

        # Step 1: Land on Page C; expect the watch bar.
        page.goto(
            f'{django_server}/workshops/ws/tutorial/page-c',
            wait_until='domcontentloaded',
        )
        bar = page.locator('[data-testid="watch-this-section"]')
        assert bar.count() == 1, 'Watch bar should be visible on page C'
        assert bar.get_attribute('href') == '/workshops/ws/video?t=16:00'
        assert 'Watch this section (16:00)' in bar.inner_text()

        # Step 2: Click the watch bar; assert URL + start=960 propagation.
        bar.click()
        page.wait_for_load_state('domcontentloaded')
        assert page.url.endswith('/workshops/ws/video?t=16:00')

        body = page.content()
        # YouTube playerVars.start is rendered with the parsed seconds.
        assert 'start: 960' in body, (
            'Expected start: 960 in playerVars when ?t=16:00'
        )

        # Step 3: The timestamps panel renders inverse links for matched rows.
        ts_panel = page.locator('[data-testid="video-chapters"]')
        assert ts_panel.count() == 1
        ts_panel.locator('summary').click()
        # Two tutorial sub-links (0:00 -> page B, 16:00 -> page C).
        tutorial_links = page.locator(
            '[data-testid="timestamp-tutorial-link"]'
        )
        assert tutorial_links.count() == 2

        # Step 4: Click the Page B sub-link (the 0:00 chapter) and land
        # on Page B with the watch bar reading "0:00".
        page_b_link = page.locator(
            '[data-testid="timestamp-tutorial-link"]'
            '[href="/workshops/ws/tutorial/page-b"]'
        )
        assert page_b_link.count() == 1
        page_b_link.click()
        page.wait_for_load_state('domcontentloaded')
        assert page.url.endswith('/workshops/ws/tutorial/page-b')
        bar = page.locator('[data-testid="watch-this-section"]')
        assert bar.count() == 1
        assert 'Watch this section (0:00)' in bar.inner_text()

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
        page.goto(
            f'{django_server}/workshops/ws/video?t=16:00',
            wait_until='domcontentloaded',
        )
        body = page.content()
        # YouTube playerVars.start carries the parsed seconds.
        assert 'start: 960' in body
        # The timestamps panel still renders (?t= doesn't suppress it).
        assert 'data-testid="video-chapters"' in body

        ctx.close()

    def test_malformed_t_does_not_break_page(
        self, browser, django_server,
    ):
        _clear_workshops()
        _create_workshop_with_timestamps()
        _create_user('main@test.com', tier_slug='main')

        ctx = _auth_context(browser, 'main@test.com')
        page = ctx.new_page()
        response = page.goto(
            f'{django_server}/workshops/ws/video?t=not-a-time',
            wait_until='domcontentloaded',
        )
        assert response is not None and response.status == 200
        body = page.content()
        # No start parameter rendered when ?t= was unparseable.
        assert 'start:' not in body

        ctx.close()
