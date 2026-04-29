"""
Playwright coverage for content-repo rendered event recap pages.

Usage:
    uv run pytest playwright_tests/test_event_recap.py -v
"""

import datetime
import os

import pytest
from django.utils import timezone

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection


def _clear_events():
    from events.models import Event

    Event.objects.all().delete()
    connection.close()


def _create_event_with_recap(slug='launch', status='completed'):
    from events.models import Event

    event = Event.objects.create(
        title='AI Shipping Labs Community Launch',
        slug=slug,
        start_datetime=timezone.now() - datetime.timedelta(days=2),
        status=status,
        recap_html=(
            '<h2>Watch the recording</h2>'
            '<section id="watch-stream">'
            '<iframe src="https://www.youtube.com/embed/WQAs1LNxdvM"></iframe>'
            '</section>'
            '<h2>What you need to know</h2>'
            '<article><h3>Execution</h3><p>Ship real projects.</p></article>'
        ),
    )
    connection.close()
    return event


@pytest.mark.django_db(transaction=True)
class TestRecapPage:
    def test_visitor_finds_rendered_recap_content(self, django_server, page):
        _clear_events()
        _create_event_with_recap()

        page.goto(f"{django_server}/events/launch/recap",
                  wait_until="domcontentloaded")
        body = page.content()

        assert 'AI Shipping Labs Community Launch' in body
        assert 'Watch the recording' in body
        assert 'youtube.com/embed/WQAs1LNxdvM' in body
        assert 'Execution' in body
        assert 'Ship real projects.' in body

    def test_event_detail_links_to_rendered_recap(self, django_server, page):
        _clear_events()
        _create_event_with_recap(status='upcoming')

        page.goto(f"{django_server}/events/launch", wait_until="domcontentloaded")
        body = page.content()
        assert 'View event recap' in body
        assert '/events/launch/recap' in body


@pytest.mark.django_db(transaction=True)
class TestEventWithoutRenderedRecap:
    def test_no_recap_link_and_404(self, django_server, page):
        _clear_events()
        from events.models import Event

        Event.objects.create(
            title='No Recap Event', slug='test-no-recap',
            start_datetime=timezone.now(),
            status='upcoming',
        )
        connection.close()

        page.goto(f"{django_server}/events/test-no-recap",
                  wait_until="domcontentloaded")
        body = page.content()
        assert 'View event recap' not in body

        response = page.goto(f"{django_server}/events/test-no-recap/recap",
                             wait_until="domcontentloaded")
        assert response.status == 404
