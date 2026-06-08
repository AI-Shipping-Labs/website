"""Issue #863: cancelled occurrences must be hidden from public surfaces and
must not inflate ``EventSeries.published_event_count``.

Covers the P1 correctness slice carved out of #863:
- ``published_event_count`` counts only ``upcoming`` / ``completed`` (excludes
  both ``draft`` and ``cancelled``).
- The public events calendar, the public events list ("all" / "past" buckets),
  and the public series page all hide cancelled occurrences from visitors.
- Staff still see cancelled occurrences on the series page so they can manage
  them.
- A regression check that cancelling an occurrence drops it from every public
  surface AND decrements the count by one.
"""

from datetime import time, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from events.models import Event, EventSeries
from events.models.event import (
    HIDDEN_FROM_PUBLIC_STATUSES,
    PUBLIC_EVENT_STATUSES,
)

User = get_user_model()


class PublishedEventCountTest(TestCase):
    """``EventSeries.published_event_count`` excludes draft AND cancelled."""

    @classmethod
    def setUpTestData(cls):
        cls.series = EventSeries.objects.create(
            name='Office Hours', start_time=time(18, 0),
        )
        now = timezone.now()
        # 6 publicly-visible occurrences (mix of upcoming + completed).
        for i in range(5):
            Event.objects.create(
                title=f'Live Session {i}', slug=f'live-session-{i}',
                start_datetime=now + timedelta(days=i + 1),
                status='upcoming',
                event_series=cls.series, series_position=i + 1,
                origin='studio',
            )
        Event.objects.create(
            title='Completed Session', slug='completed-session',
            start_datetime=now - timedelta(days=10),
            status='completed',
            event_series=cls.series, series_position=6, origin='studio',
        )
        # 3 cancelled occurrences (the live symptom: Jun 22 / Jul 13 / Jul 20).
        for i in range(3):
            Event.objects.create(
                title=f'Cancelled Session {i}', slug=f'cancelled-session-{i}',
                start_datetime=now + timedelta(days=20 + i),
                status='cancelled',
                event_series=cls.series, series_position=7 + i,
                origin='studio',
            )

    def test_status_sets_are_disjoint_and_cover_choices(self):
        # Guard against drift: the two sets must not overlap, and together they
        # must account for every Event status choice.
        self.assertEqual(
            HIDDEN_FROM_PUBLIC_STATUSES & PUBLIC_EVENT_STATUSES, set(),
        )
        self.assertEqual(
            HIDDEN_FROM_PUBLIC_STATUSES | PUBLIC_EVENT_STATUSES,
            {'draft', 'upcoming', 'completed', 'cancelled'},
        )

    def test_count_excludes_cancelled_and_draft(self):
        # 9 total occurrences, 3 cancelled -> count is 6, not 9.
        self.assertEqual(self.series.event_count, 9)
        self.assertEqual(self.series.published_event_count, 6)

    def test_draft_also_excluded_from_count(self):
        Event.objects.create(
            title='Draft Session', slug='draft-session',
            start_datetime=timezone.now() + timedelta(days=30),
            status='draft',
            event_series=self.series, series_position=10, origin='studio',
        )
        # Adding a draft does not raise the published count.
        self.assertEqual(self.series.published_event_count, 6)

    def test_cancelling_decrements_count(self):
        live = self.series.events.filter(status='upcoming').first()
        live.status = 'cancelled'
        live.save()
        self.assertEqual(self.series.published_event_count, 5)


class CancelledHiddenFromCalendarTest(TestCase):
    """Cancelled occurrences must not appear on the public events calendar."""

    @classmethod
    def setUpTestData(cls):
        # Anchor on a fixed month so the calendar URL is deterministic.
        cls.month_start = timezone.now().replace(
            day=15, hour=12, minute=0, second=0, microsecond=0,
        )
        cls.year = cls.month_start.year
        cls.month = cls.month_start.month
        cls.live = Event.objects.create(
            title='Calendar Live Session', slug='calendar-live',
            start_datetime=cls.month_start,
            status='upcoming', origin='studio',
        )
        cls.cancelled = Event.objects.create(
            title='Calendar Cancelled Session', slug='calendar-cancelled',
            start_datetime=cls.month_start + timedelta(days=1),
            status='cancelled', origin='studio',
        )

    def test_cancelled_event_absent_from_calendar(self):
        url = f'/events/calendar/{self.year}/{self.month}'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Calendar Live Session')
        self.assertNotContains(response, 'Calendar Cancelled Session')


class CancelledHiddenFromEventsListTest(TestCase):
    """Cancelled occurrences must not appear in any public events listing."""

    @classmethod
    def setUpTestData(cls):
        now = timezone.now()
        cls.upcoming_live = Event.objects.create(
            title='List Upcoming Live', slug='list-upcoming-live',
            start_datetime=now + timedelta(days=5),
            status='upcoming', origin='studio',
        )
        cls.past_live = Event.objects.create(
            title='List Past Live', slug='list-past-live',
            start_datetime=now - timedelta(days=5),
            end_datetime=now - timedelta(days=5, hours=-1),
            status='completed', origin='studio',
        )
        # A cancelled occurrence dated in the past (the old code added these
        # back into the "all" past bucket via Q(status='cancelled')).
        cls.cancelled_past = Event.objects.create(
            title='List Cancelled Past', slug='list-cancelled-past',
            start_datetime=now - timedelta(days=3),
            status='cancelled', origin='studio',
        )
        # A cancelled occurrence dated in the future.
        cls.cancelled_future = Event.objects.create(
            title='List Cancelled Future', slug='list-cancelled-future',
            start_datetime=now + timedelta(days=8),
            status='cancelled', origin='studio',
        )

    def test_cancelled_absent_from_all_view(self):
        response = self.client.get('/events?filter=all')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'List Cancelled Past')
        self.assertNotContains(response, 'List Cancelled Future')
        # The live upcoming occurrence is still visible.
        self.assertContains(response, 'List Upcoming Live')

    def test_cancelled_absent_from_past_view(self):
        response = self.client.get('/events?filter=past')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'List Cancelled Past')

    def test_cancelled_absent_from_upcoming_view(self):
        response = self.client.get('/events?filter=upcoming')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'List Cancelled Future')
        self.assertContains(response, 'List Upcoming Live')

    def test_no_cancelled_badge_shown_to_visitor(self):
        # Cancelled is hidden, never labelled, on the public listing.
        response = self.client.get('/events?filter=all')
        self.assertNotContains(response, 'List Cancelled Past')
        self.assertNotContains(response, 'List Cancelled Future')


class CancelledHiddenFromSeriesPageTest(TestCase):
    """Public series page hides cancelled occurrences; staff still see them."""

    @classmethod
    def setUpTestData(cls):
        cls.series = EventSeries.objects.create(
            name='Series Visibility', start_time=time(18, 0),
        )
        now = timezone.now()
        cls.live = Event.objects.create(
            title='Visible Live Occurrence', slug='visible-live-occurrence',
            start_datetime=now + timedelta(days=2),
            status='upcoming',
            event_series=cls.series, series_position=1, origin='studio',
        )
        cls.cancelled = Event.objects.create(
            title='Hidden Cancelled Occurrence',
            slug='hidden-cancelled-occurrence',
            start_datetime=now + timedelta(days=3),
            status='cancelled',
            event_series=cls.series, series_position=2, origin='studio',
        )

    def test_anonymous_does_not_see_cancelled(self):
        response = self.client.get(f'/events/groups/{self.series.slug}')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Visible Live Occurrence')
        self.assertNotContains(response, 'Hidden Cancelled Occurrence')

    def test_anonymous_sees_no_cancelled_label(self):
        response = self.client.get(f'/events/groups/{self.series.slug}')
        # The cancelled occurrence title is absent (hidden, not badged).
        self.assertNotContains(response, 'Hidden Cancelled Occurrence')

    def test_non_staff_user_does_not_see_cancelled(self):
        member = User.objects.create_user(
            email='member@test.com', password='pass',
        )
        self.client.force_login(member)
        response = self.client.get(f'/events/groups/{self.series.slug}')
        self.assertNotContains(response, 'Hidden Cancelled Occurrence')

    def test_staff_still_sees_cancelled(self):
        staff = User.objects.create_user(
            email='staff863@test.com', password='pass', is_staff=True,
        )
        self.client.force_login(staff)
        response = self.client.get(f'/events/groups/{self.series.slug}')
        self.assertContains(response, 'Hidden Cancelled Occurrence')


class CancellingDropsFromAllSurfacesRegressionTest(TestCase):
    """Regression: cancelling an occurrence drops it from each public surface
    and decrements ``published_event_count`` by one.
    """

    @classmethod
    def setUpTestData(cls):
        cls.series = EventSeries.objects.create(
            name='Regression Series', start_time=time(18, 0),
        )
        cls.month_start = timezone.now().replace(
            day=12, hour=12, minute=0, second=0, microsecond=0,
        )
        cls.target = Event.objects.create(
            title='Regression Target Occurrence',
            slug='regression-target-occurrence',
            start_datetime=cls.month_start,
            status='upcoming',
            event_series=cls.series, series_position=1, origin='studio',
        )

    def _series_page_shows_target(self):
        response = self.client.get(f'/events/groups/{self.series.slug}')
        return b'Regression Target Occurrence' in response.content

    def _calendar_shows_target(self):
        url = (
            f'/events/calendar/{self.month_start.year}/'
            f'{self.month_start.month}'
        )
        response = self.client.get(url)
        return b'Regression Target Occurrence' in response.content

    def _list_shows_target(self):
        response = self.client.get('/events?filter=all')
        return b'Regression Target Occurrence' in response.content

    def test_cancelling_removes_from_every_surface_and_count(self):
        # Before: visible everywhere, counted.
        self.assertTrue(self._series_page_shows_target())
        self.assertTrue(self._calendar_shows_target())
        self.assertTrue(self._list_shows_target())
        self.assertEqual(self.series.published_event_count, 1)

        # Cancel the occurrence.
        self.target.status = 'cancelled'
        self.target.save()

        # After: gone from every public surface, count decremented.
        self.assertFalse(self._series_page_shows_target())
        self.assertFalse(self._calendar_shows_target())
        self.assertFalse(self._list_shows_target())
        self.assertEqual(self.series.published_event_count, 0)
