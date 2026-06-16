from datetime import UTC, datetime, timedelta

from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from events.models import Event
from events.services.display_time import format_event_time_range
from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting


class EventTimeDisplayServiceTest(TestCase):
    def test_formats_inline_range_in_requested_timezone(self):
        start = datetime(2026, 4, 13, 16, 30, tzinfo=UTC)
        end = datetime(2026, 4, 13, 18, 0, tzinfo=UTC)

        result = format_event_time_range(start, end, 'Europe/Berlin')

        self.assertEqual(
            result,
            'April 13, 2026, 18:30-20:00 Europe/Berlin',
        )

    def test_formats_start_without_end(self):
        start = datetime(2026, 4, 13, 16, 30, tzinfo=UTC)

        result = format_event_time_range(start, None, 'Europe/Berlin')

        self.assertEqual(result, 'April 13, 2026, 18:30 Europe/Berlin')

    def test_formats_range_across_local_dates(self):
        start = datetime(2026, 4, 13, 22, 30, tzinfo=UTC)
        end = datetime(2026, 4, 14, 1, 0, tzinfo=UTC)

        result = format_event_time_range(start, end, 'UTC')

        self.assertEqual(
            result,
            'April 13, 2026, 22:30 - April 14, 2026, 01:00 UTC',
        )

    def test_invalid_timezone_falls_back_to_berlin(self):
        start = datetime(2026, 4, 13, 16, 30, tzinfo=UTC)

        result = format_event_time_range(start, None, 'Invalid/Zone')

        self.assertEqual(result, 'April 13, 2026, 18:30 Europe/Berlin')


class EventDetailTimeDisplayTest(TestCase):
    def setUp(self):
        clear_config_cache()

    def tearDown(self):
        clear_config_cache()

    def test_detail_renders_default_timezone_fallback_from_site_setting(self):
        IntegrationSetting.objects.create(
            key='EVENT_DISPLAY_TIMEZONE',
            value='America/New_York',
            group='site',
            is_secret=False,
        )
        clear_config_cache()
        event = Event.objects.create(
            title='Local Time Event',
            slug='local-time-event',
            start_datetime=datetime(2026, 4, 13, 16, 30, tzinfo=UTC),
            end_datetime=datetime(2026, 4, 13, 18, 0, tzinfo=UTC),
            status='upcoming',
            timezone='Europe/Berlin',
            location='Zoom',
        )

        response = self.client.get(event.get_absolute_url())

        self.assertContains(
            response,
            'April 13, 2026, 12:30-14:00 America/New_York',
        )
        self.assertContains(response, 'data-default-timezone="America/New_York"')
        self.assertContains(response, 'data-browser-timezone-enabled="true"')
        self.assertContains(response, 'data-start-utc="2026-04-13T16:30:00Z"')
        self.assertContains(response, 'data-end-utc="2026-04-13T18:00:00Z"')
        self.assertNotContains(response, 'Until 18:00 UTC')

    def test_detail_uses_berlin_fallback_when_setting_unset(self):
        event = Event.objects.create(
            title='Berlin Fallback Event',
            slug='berlin-fallback-event',
            start_datetime=datetime(2026, 4, 13, 16, 30, tzinfo=UTC),
            end_datetime=datetime(2026, 4, 13, 18, 0, tzinfo=UTC),
            status='upcoming',
        )

        response = self.client.get(event.get_absolute_url())

        self.assertContains(
            response,
            'April 13, 2026, 18:30-20:00 Europe/Berlin',
        )
        self.assertNotContains(response, 'event-timezone-select')
        self.assertNotContains(response, 'data-event-timezone-select')

    def test_logged_in_preferred_timezone_takes_server_precedence(self):
        user = User.objects.create_user(
            email='ny@example.com',
            preferred_timezone='America/New_York',
        )
        self.client.force_login(user)
        event = Event.objects.create(
            title='Preferred Timezone Event',
            slug='preferred-timezone-event',
            start_datetime=datetime(2026, 4, 13, 16, 30, tzinfo=UTC),
            end_datetime=datetime(2026, 4, 13, 18, 0, tzinfo=UTC),
            status='upcoming',
            timezone='Europe/Berlin',
        )

        response = self.client.get(event.get_absolute_url())

        self.assertContains(
            response,
            'April 13, 2026, 12:30-14:00 America/New_York',
        )
        self.assertContains(response, 'data-default-timezone="America/New_York"')
        self.assertContains(response, 'data-browser-timezone-enabled="false"')
        self.assertNotContains(response, 'event-timezone-select')

    def test_signed_in_attendee_sees_own_timezone_not_event_timezone_or_utc(self):
        user = User.objects.create_user(
            email='kolkata@example.com',
            preferred_timezone='Asia/Kolkata',
        )
        self.client.force_login(user)
        event = Event.objects.create(
            title='Attendee Timezone Event',
            slug='attendee-timezone-event',
            start_datetime=datetime(2026, 4, 13, 16, 30, tzinfo=UTC),
            end_datetime=datetime(2026, 4, 13, 18, 0, tzinfo=UTC),
            status='upcoming',
            timezone='Europe/Berlin',
        )

        response = self.client.get(event.get_absolute_url())

        self.assertContains(
            response,
            'April 13, 2026, 22:00-23:30 Asia/Kolkata',
        )
        self.assertContains(response, 'data-default-timezone="Asia/Kolkata"')
        self.assertContains(response, 'data-browser-timezone-enabled="false"')
        self.assertNotContains(response, 'Europe/Berlin')

    def test_logged_in_without_preference_allows_browser_timezone_replacement(self):
        user = User.objects.create_user(email='browser@example.com')
        self.client.force_login(user)
        event = Event.objects.create(
            title='Browser Timezone Event',
            slug='browser-timezone-event',
            start_datetime=datetime(2026, 4, 13, 16, 30, tzinfo=UTC),
            end_datetime=datetime(2026, 4, 13, 18, 0, tzinfo=UTC),
            status='upcoming',
        )

        response = self.client.get(event.get_absolute_url())

        self.assertContains(response, 'data-browser-timezone-enabled="true"')

    def test_detail_uses_berlin_fallback_when_setting_invalid(self):
        IntegrationSetting.objects.create(
            key='EVENT_DISPLAY_TIMEZONE',
            value='Invalid/Zone',
            group='site',
            is_secret=False,
        )
        clear_config_cache()
        event = Event.objects.create(
            title='Invalid Setting Event',
            slug='invalid-setting-event',
            start_datetime=datetime(2026, 4, 13, 16, 30, tzinfo=UTC),
            status='upcoming',
        )

        response = self.client.get(event.get_absolute_url())

        self.assertContains(response, 'April 13, 2026, 18:30 Europe/Berlin')
        self.assertContains(response, 'data-default-timezone="Europe/Berlin"')

    def test_completed_zoom_location_is_hidden(self):
        event = Event.objects.create(
            title='Completed Zoom Event',
            slug='completed-zoom-event',
            start_datetime=timezone.now() - timedelta(days=7),
            status='completed',
            location='Zoom',
        )

        response = self.client.get(event.get_absolute_url())

        self.assertContains(response, 'Completed Zoom Event')
        self.assertNotContains(response, '<i data-lucide="map-pin"')
        self.assertNotContains(response, '>Zoom<')

    def test_upcoming_zoom_location_is_preserved(self):
        event = Event.objects.create(
            title='Upcoming Zoom Event',
            slug='upcoming-zoom-event',
            start_datetime=timezone.now() + timedelta(days=7),
            status='upcoming',
            location='Zoom',
        )

        response = self.client.get(event.get_absolute_url())

        self.assertContains(response, '<i data-lucide="map-pin"')
        self.assertContains(response, 'Zoom')

    def test_completed_custom_location_is_preserved(self):
        event = Event.objects.create(
            title='Completed In Person Event',
            slug='completed-in-person-event',
            start_datetime=timezone.now() - timedelta(days=7),
            status='completed',
            location='Berlin office',
        )

        response = self.client.get(event.get_absolute_url())

        self.assertContains(response, '<i data-lucide="map-pin"')
        self.assertContains(response, 'Berlin office')


class EventListAndCalendarScopeTest(TestCase):
    def test_list_and_calendar_are_date_only_and_link_to_detail(self):
        # Issue #713: use a future start so the event lands in the
        # Upcoming section (which shows ``formatted_date`` per the
        # template). Otherwise the row falls into the compact past
        # list that renders ``short_date``.
        future = timezone.now() + timedelta(days=30)
        future = future.replace(hour=16, minute=30, second=0, microsecond=0)
        event = Event.objects.create(
            title='Detail Owns Timezone Event',
            slug='detail-owns-timezone-event',
            start_datetime=future,
            status='upcoming',
        )

        list_response = self.client.get('/events')
        calendar_response = self.client.get(
            f'/events/calendar/{future.year}/{future.month}',
        )

        # Issue #673: links go to the canonical id+slug URL now.
        self.assertContains(list_response, event.get_absolute_url())
        self.assertContains(calendar_response, event.get_absolute_url())
        self.assertContains(list_response, event.formatted_date())
        self.assertNotContains(list_response, event.formatted_start())
        self.assertNotContains(calendar_response, event.formatted_time())
