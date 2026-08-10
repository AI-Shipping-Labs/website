"""Issue #1382 — Luma-style date-grouped events timeline on /events.

Covers the presentation behaviors introduced by the redesign:
- default view is Upcoming only (Past lives behind ?filter=past),
- events are grouped into ordered per-date buckets,
- a series occurrence renders the "Weekly series" badge + cadence line and
  collapses to the next occurrence (never lists every session),
- a right-side thumbnail renders only for an authored cover image, never the
  auto-generated banner,
- past recordings show the "Watch recording" CTA.
"""

from datetime import UTC, datetime, time, timedelta

from django.test import TestCase
from django.utils import timezone

from events.models import Event, EventSeries


def _future_utc(days, hour=9):
    """Return a future UTC datetime ``days`` ahead at a fixed hour."""
    base = (timezone.now() + timedelta(days=days)).astimezone(UTC)
    return base.replace(hour=hour, minute=0, second=0, microsecond=0)


class TimelineDefaultUpcomingOnlyTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.upcoming = Event.objects.create(
            title='Future Session',
            slug='future-session-1382',
            start_datetime=_future_utc(3),
            timezone='UTC',
            status='upcoming',
        )
        cls.past = Event.objects.create(
            title='Old Session',
            slug='old-session-1382',
            start_datetime=timezone.now() - timedelta(days=3),
            status='completed',
            recording_url='https://video.test/old',
        )

    def test_default_view_shows_only_upcoming(self):
        response = self.client.get('/events')
        self.assertContains(response, 'Future Session')
        self.assertNotContains(response, 'Old Session')
        self.assertContains(response, 'data-testid="events-upcoming-section"')
        self.assertNotContains(response, 'data-testid="events-past-section"')

    def test_past_toggle_shows_only_past(self):
        response = self.client.get('/events?filter=past')
        self.assertContains(response, 'Old Session')
        self.assertNotContains(response, 'Future Session')
        self.assertContains(response, 'data-testid="events-past-section"')

    def test_toggle_marks_active_view(self):
        upcoming = self.client.get('/events')
        past = self.client.get('/events?filter=past')
        self.assertEqual(upcoming.context['filter_mode'], 'upcoming')
        self.assertEqual(past.context['filter_mode'], 'past')
        # The active pill carries aria-current="page".
        self.assertContains(
            upcoming,
            'data-testid="events-filter-upcoming"',
        )
        self.assertContains(past, 'aria-current="page"')

    def test_empty_upcoming_uses_member_empty_state(self):
        Event.objects.all().delete()
        response = self.client.get('/events')
        self.assertContains(response, 'No upcoming events yet')


class TimelineDateGroupingTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        # Two events share one date; a third lands on a later date.
        cls.morning = Event.objects.create(
            title='Morning Standup',
            slug='morning-standup-1382',
            start_datetime=_future_utc(2, hour=9),
            timezone='UTC',
            status='upcoming',
        )
        cls.afternoon = Event.objects.create(
            title='Afternoon Deep Dive',
            slug='afternoon-deep-dive-1382',
            start_datetime=_future_utc(2, hour=15),
            timezone='UTC',
            status='upcoming',
        )
        cls.later = Event.objects.create(
            title='Later Workshop',
            slug='later-workshop-1382',
            start_datetime=_future_utc(5, hour=9),
            timezone='UTC',
            status='upcoming',
        )

    def test_events_grouped_into_ordered_date_buckets(self):
        response = self.client.get('/events')
        days = response.context['upcoming_days']

        self.assertEqual(len(days), 2)
        # Ascending chronological order.
        self.assertLess(days[0]['iso_date'], days[1]['iso_date'])
        # Same-date events share a bucket.
        self.assertEqual(len(days[0]['rows']), 2)
        self.assertEqual(len(days[1]['rows']), 1)

    def test_earlier_date_rail_renders_before_later_date_rail(self):
        response = self.client.get('/events')
        body = response.content.decode()
        days = response.context['upcoming_days']
        self.assertLess(
            body.index(days[0]['date_label']),
            body.index(days[1]['date_label']),
        )

    def test_each_card_shows_a_clock_time(self):
        response = self.client.get('/events')
        self.assertContains(response, 'data-testid="event-card-time"')
        # 12-hour meridiem labels from the view helper.
        self.assertContains(response, ' AM')


class TimelineSeriesCardTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.series = EventSeries.objects.create(
            name='Build Club',
            slug='build-club-1382',
            cadence='weekly',
            day_of_week=0,  # Monday
            start_time=time(18, 0),
            timezone='UTC',
        )
        for i in range(3):
            Event.objects.create(
                title=f'Build Club Session {i}',
                slug=f'build-club-session-{i}-1382',
                start_datetime=_future_utc(2 + i * 7, hour=18),
                timezone='UTC',
                status='upcoming',
                origin='studio',
                event_series=cls.series,
                series_position=i + 1,
            )

    def test_series_collapses_to_one_card_with_weekly_badge_and_cadence(self):
        response = self.client.get('/events')
        self.assertContains(response, 'data-testid="event-series-card"')
        self.assertContains(response, 'Weekly series')
        self.assertContains(
            response, 'Every Monday · part of Build Club',
        )
        self.assertContains(response, '3 upcoming sessions')

    def test_series_card_does_not_list_every_session(self):
        response = self.client.get('/events')
        # Only the next occurrence is previewed.
        self.assertContains(response, 'Build Club Session 0')
        self.assertNotContains(response, 'Build Club Session 1')
        self.assertNotContains(response, 'Build Club Session 2')
        # Exactly one series card for the three occurrences.
        self.assertEqual(
            response.content.decode().count('data-testid="event-series-card"'),
            1,
        )


class TimelineThumbnailTest(TestCase):
    def test_authored_cover_renders_thumbnail(self):
        Event.objects.create(
            title='Covered Event',
            slug='covered-event-1382',
            start_datetime=_future_utc(2),
            timezone='UTC',
            status='upcoming',
            cover_image_url='https://cdn.aishippinglabs.com/events/cover.jpg',
        )
        response = self.client.get('/events')
        self.assertContains(response, 'data-testid="event-card-thumbnail"')
        self.assertContains(
            response, 'https://cdn.aishippinglabs.com/events/cover.jpg',
        )

    def test_auto_banner_is_not_used_as_thumbnail(self):
        Event.objects.create(
            title='Auto Banner Only Event',
            slug='auto-banner-only-1382',
            start_datetime=_future_utc(2),
            timezone='UTC',
            status='upcoming',
            auto_banner_url='https://cdn.aishippinglabs.com/events/auto.png',
        )
        response = self.client.get('/events')
        self.assertNotContains(response, 'data-testid="event-card-thumbnail"')
        self.assertNotContains(
            response, 'https://cdn.aishippinglabs.com/events/auto.png',
        )


class TimelinePastRecordingCtaTest(TestCase):
    def test_past_event_with_recording_shows_watch_cta(self):
        event = Event.objects.create(
            title='Recorded Talk',
            slug='recorded-talk-1382',
            start_datetime=datetime(2026, 2, 2, 18, 0, tzinfo=UTC),
            status='completed',
            recording_url='https://youtu.be/rec1382',
            published=True,
        )
        response = self.client.get('/events?filter=past')
        self.assertContains(response, 'data-testid="past-card-recording-cta"')
        self.assertContains(response, 'Watch recording')
        self.assertContains(response, event.get_absolute_url())

    def test_upcoming_event_has_no_watch_cta(self):
        Event.objects.create(
            title='Future Talk',
            slug='future-talk-1382',
            start_datetime=_future_utc(3),
            timezone='UTC',
            status='upcoming',
        )
        response = self.client.get('/events')
        self.assertNotContains(response, 'Watch recording')
