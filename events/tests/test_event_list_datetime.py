from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from events.models import Event, EventSeries
from events.services.timeline import event_local_datetime, format_time_label


def _expected_time_label(event, tz_name=None):
    """Return the timeline clock label the view would render for ``event``.

    ``tz_name`` is the authenticated viewer's zone; ``None`` uses the event's
    own stored timezone (anonymous behavior).
    """
    viewer_tz = ZoneInfo(tz_name) if tz_name else None
    local = event_local_datetime(
        event.start_datetime, event.timezone, viewer_tz,
    )
    return format_time_label(local)


def _future_start(*, days=12, hour=16, minute=0):
    value = timezone.now() + timedelta(days=days)
    return value.astimezone(UTC).replace(
        hour=hour,
        minute=minute,
        second=0,
        microsecond=0,
    )


def _past_start(*, days=12, hour=16, minute=0):
    value = timezone.now() - timedelta(days=days)
    return value.astimezone(UTC).replace(
        hour=hour,
        minute=minute,
        second=0,
        microsecond=0,
    )


def _create_event(title, slug, *, start_datetime, **overrides):
    defaults = {
        'title': title,
        'slug': slug,
        'start_datetime': start_datetime,
        'end_datetime': start_datetime + timedelta(hours=1),
        'status': 'upcoming',
        'timezone': 'Europe/Berlin',
        'location': 'Zoom',
    }
    defaults.update(overrides)
    return Event.objects.create(**defaults)


class PublicEventListDatetimeTest(TestCase):
    def test_anonymous_upcoming_standalone_card_uses_event_timezone_clock(self):
        event = _create_event(
            'Mock Interviews for AI Engineering Roles',
            'mock-interviews-ai-engineering-roles',
            start_datetime=_future_start(),
        )

        all_response = self.client.get('/events')
        upcoming_response = self.client.get('/events?filter=upcoming')
        expected = _expected_time_label(event)

        self.assertContains(all_response, expected)
        self.assertContains(upcoming_response, expected)
        self.assertContains(all_response, 'data-testid="timeline-day-date"')

    def test_authenticated_preferred_timezone_converts_standalone_card(self):
        user = User.objects.create_user(
            email='ny-list-card@example.com',
            preferred_timezone='America/New_York',
        )
        self.client.force_login(user)
        event = _create_event(
            'New York Local List Time',
            'new-york-local-list-time',
            start_datetime=_future_start(hour=16),
        )

        response = self.client.get('/events')

        expected = _expected_time_label(event, tz_name='America/New_York')
        self.assertContains(response, expected)

    def test_authenticated_without_valid_timezone_uses_utc_fallback(self):
        user = User.objects.create_user(
            email='utc-list-card@example.com',
            preferred_timezone='Not/AZone',
        )
        self.client.force_login(user)
        event = _create_event(
            'UTC Fallback List Time',
            'utc-fallback-list-time',
            start_datetime=_future_start(hour=16),
        )

        response = self.client.get('/events?filter=upcoming')

        # start_datetime is 16:00 UTC; the compact label stays 24-hour.
        expected = _expected_time_label(event, tz_name='UTC')
        self.assertContains(response, expected)
        self.assertContains(response, '16:00')

    def test_single_occurrence_series_fallback_uses_single_event_datetime(self):
        series = EventSeries.objects.create(
            name='One Session Series',
            slug='one-session-series',
            start_time=datetime(2026, 1, 1, 18, 0).time(),
        )
        event = _create_event(
            'One Session Series Kickoff',
            'one-session-series-kickoff',
            start_datetime=_future_start(days=10, hour=16),
            event_series=series,
        )

        response = self.client.get('/events?filter=upcoming')

        self.assertContains(response, 'data-testid="series-cadence-line"')
        self.assertContains(response, 'part of One Session Series')
        self.assertContains(response, _expected_time_label(event))
        self.assertNotContains(response, 'data-testid="event-series-card"')

    def test_past_list_cards_use_event_timezone_clock(self):
        rich_event = _create_event(
            'Rich Past Recording Time',
            'rich-past-recording-time',
            start_datetime=_past_start(days=3, hour=15),
            end_datetime=_past_start(days=3, hour=16),
            status='completed',
            location='',
            recording_url='https://youtube.com/watch?v=listtime',
            published=True,
        )

        past_response = self.client.get('/events?filter=past')

        self.assertContains(past_response, _expected_time_label(rich_event))
        self.assertContains(past_response, 'data-testid="timeline-day-date"')

    def test_grouped_series_card_keeps_datetime_and_series_metadata(self):
        series = EventSeries.objects.create(
            name='Grouped Weekly Series',
            slug='grouped-weekly-series',
            start_time=datetime(2026, 1, 1, 18, 0).time(),
        )
        first = _create_event(
            'Grouped Weekly Series Session 1',
            'grouped-weekly-series-session-1',
            start_datetime=_future_start(days=7, hour=16),
            event_series=series,
        )
        _create_event(
            'Grouped Weekly Series Session 2',
            'grouped-weekly-series-session-2',
            start_datetime=_future_start(days=14, hour=16),
            event_series=series,
        )

        response = self.client.get('/events?filter=upcoming')

        self.assertContains(response, 'data-testid="event-series-card"')
        self.assertContains(response, _expected_time_label(first))
        self.assertContains(response, '2 upcoming sessions')
        self.assertContains(response, 'Grouped Weekly Series')
