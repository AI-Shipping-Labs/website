"""Playwright coverage for centralized event time-window querysets (#1022)."""

import datetime
import os

import pytest
from django.utils import timezone

from playwright_tests.conftest import auth_context as _auth_context
from playwright_tests.conftest import create_user as _create_user

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection

pytestmark = pytest.mark.local_only


def _clear_event_data():
    from content.models import Workshop, WorkshopPage
    from events.models import Event, EventRegistration

    EventRegistration.objects.all().delete()
    WorkshopPage.objects.all().delete()
    Workshop.objects.all().delete()
    Event.objects.all().delete()
    connection.close()


def _create_event(
    *,
    title,
    slug,
    start_datetime,
    end_datetime=None,
    status='upcoming',
    published=True,
    recording_url='',
):
    from events.models import Event

    event = Event.objects.create(
        title=title,
        slug=slug,
        start_datetime=start_datetime,
        end_datetime=end_datetime,
        status=status,
        published=published,
        recording_url=recording_url,
    )
    connection.close()
    return event


def _register(user, event):
    from events.models import EventRegistration

    EventRegistration.objects.create(user=user, event=event)
    connection.close()


@pytest.mark.django_db(transaction=True)
class TestVisitorEventTimeWindows1022:
    @pytest.mark.core
    def test_visitor_switches_upcoming_and_past_event_filters(self, django_server, page):
        _clear_event_data()
        now = timezone.now()
        _create_event(
            title='Upcoming Workshop 1022',
            slug='upcoming-workshop-1022',
            start_datetime=now + datetime.timedelta(days=3),
            end_datetime=now + datetime.timedelta(days=3, hours=1),
            status='upcoming',
        )
        _create_event(
            title='Completed Future Hidden 1022',
            slug='completed-future-hidden-1022',
            start_datetime=now + datetime.timedelta(days=4),
            end_datetime=now + datetime.timedelta(days=4, hours=1),
            status='completed',
        )
        _create_event(
            title='Finished Recording 1022',
            slug='finished-recording-1022',
            start_datetime=now - datetime.timedelta(days=3),
            end_datetime=now - datetime.timedelta(days=3, hours=-1),
            status='completed',
            recording_url='https://video.test/finished-1022',
        )

        page.goto(f'{django_server}/events', wait_until='domcontentloaded')
        body = page.content()
        assert 'Upcoming Workshop 1022' in body
        assert 'Completed Future Hidden 1022' not in body
        assert 'Finished Recording 1022' in body

        page.locator('[data-testid="events-filter-upcoming"]').click()
        page.wait_for_load_state('domcontentloaded')
        body = page.content()
        assert 'Upcoming Workshop 1022' in body
        assert 'Completed Future Hidden 1022' not in body
        assert 'Finished Recording 1022' not in body

        page.locator('[data-testid="events-filter-past"]').click()
        page.wait_for_load_state('domcontentloaded')
        body = page.content()
        assert 'Finished Recording 1022' in body
        assert 'Upcoming Workshop 1022' not in body
        assert 'Completed Future Hidden 1022' not in body


@pytest.mark.django_db(transaction=True)
class TestDashboardEventTimeWindows1022:
    @pytest.mark.core
    def test_dashboard_lists_only_eligible_registered_future_events(
        self, django_server, browser
    ):
        _clear_event_data()
        user = _create_user('events-1022@test.com', tier_slug='main')
        now = timezone.now()

        first = _create_event(
            title='Soon Eligible 1022',
            slug='soon-eligible-1022',
            start_datetime=now + datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1, hours=1),
        )
        second = _create_event(
            title='Later Eligible 1022',
            slug='later-eligible-1022',
            start_datetime=now + datetime.timedelta(days=2),
            end_datetime=now + datetime.timedelta(days=2, hours=1),
        )
        completed_future = _create_event(
            title='Completed Future Dashboard 1022',
            slug='completed-future-dashboard-1022',
            start_datetime=now + datetime.timedelta(hours=12),
            end_datetime=now + datetime.timedelta(hours=13),
            status='completed',
        )
        draft = _create_event(
            title='Draft Hidden Dashboard 1022',
            slug='draft-hidden-dashboard-1022',
            start_datetime=now + datetime.timedelta(hours=2),
            end_datetime=now + datetime.timedelta(hours=3),
            status='draft',
        )
        cancelled = _create_event(
            title='Cancelled Hidden Dashboard 1022',
            slug='cancelled-hidden-dashboard-1022',
            start_datetime=now + datetime.timedelta(hours=3),
            end_datetime=now + datetime.timedelta(hours=4),
            status='cancelled',
        )
        past = _create_event(
            title='Past Hidden Dashboard 1022',
            slug='past-hidden-dashboard-1022',
            start_datetime=now - datetime.timedelta(days=2),
            end_datetime=now - datetime.timedelta(days=2, hours=-1),
            status='completed',
        )

        for event in [first, second, completed_future, draft, cancelled, past]:
            _register(user, event)

        context = _auth_context(browser, 'events-1022@test.com')
        page = context.new_page()
        page.goto(f'{django_server}/', wait_until='domcontentloaded')
        body = page.content()

        assert 'Soon Eligible 1022' in body
        assert 'Later Eligible 1022' in body
        assert 'Completed Future Dashboard 1022' not in body
        assert 'Draft Hidden Dashboard 1022' not in body
        assert 'Cancelled Hidden Dashboard 1022' not in body
        assert 'Past Hidden Dashboard 1022' not in body

        first_pos = body.index('Soon Eligible 1022')
        second_pos = body.index('Later Eligible 1022')
        assert first_pos < second_pos
