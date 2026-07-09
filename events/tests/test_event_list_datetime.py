from datetime import UTC, datetime, timedelta

from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from accounts.services.timezones import format_user_datetime
from accounts.templatetags.date_formatting import event_source_short_datetime
from events.models import Event, EventSeries

LIST_CARD_USER_FORMAT = '%a, %b %d, %Y, %H:%M'


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
    def test_anonymous_upcoming_standalone_card_uses_source_short_datetime(self):
        event = _create_event(
            'Mock Interviews for AI Engineering Roles',
            'mock-interviews-ai-engineering-roles',
            start_datetime=_future_start(),
        )

        all_response = self.client.get('/events')
        upcoming_response = self.client.get('/events?filter=upcoming')
        expected = event_source_short_datetime(event)

        self.assertContains(all_response, expected)
        self.assertContains(upcoming_response, expected)
        self.assertIn('·', expected)

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

        expected = format_user_datetime(
            event.start_datetime,
            user,
            fmt=LIST_CARD_USER_FORMAT,
        )
        self.assertContains(response, expected)
        self.assertNotContains(response, event_source_short_datetime(event))

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

        expected = format_user_datetime(
            event.start_datetime,
            user,
            fmt=LIST_CARD_USER_FORMAT,
        )
        self.assertContains(response, expected)
        self.assertIn(' UTC', expected)

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

        self.assertContains(response, 'data-testid="event-card-series-link"')
        self.assertContains(response, 'Series: One Session Series')
        self.assertContains(response, event_source_short_datetime(event))
        self.assertNotContains(response, 'data-testid="event-series-card"')

    def test_past_list_cards_use_source_short_datetime(self):
        compact_event = _create_event(
            'Compact Past List Time',
            'compact-past-list-time',
            start_datetime=_past_start(days=4, hour=15),
            end_datetime=_past_start(days=4, hour=16),
            status='completed',
            location='',
        )
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

        all_response = self.client.get('/events')
        past_response = self.client.get('/events?filter=past')

        self.assertContains(
            all_response,
            event_source_short_datetime(compact_event),
        )
        self.assertContains(
            past_response,
            event_source_short_datetime(rich_event),
        )

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
        self.assertContains(response, event_source_short_datetime(first))
        self.assertContains(response, '2 upcoming sessions')
        self.assertContains(response, 'data-testid="series-card-see-more"')
