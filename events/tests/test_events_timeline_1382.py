"""Issue #1382 — Luma-style date-grouped events timeline on /events.

Covers the presentation behaviors introduced by the redesign:
- default view is Upcoming only (Past lives behind ?filter=past),
- events are grouped into ordered per-date buckets,
- a series renders its stable title, access, session count, and next time in
  one row rather than listing every occurrence,
- event rows stay text-first even when authored or generated banners exist,
- past events show recordings with a "Watch recording" CTA when available.
"""

from datetime import UTC, datetime, time, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from events.models import Event, EventSeries

User = get_user_model()


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

    def test_legacy_and_unknown_filters_resolve_to_upcoming(self):
        for filter_value in ('all', 'unknown'):
            with self.subTest(filter_value=filter_value):
                response = self.client.get(f'/events?filter={filter_value}')
                self.assertEqual(response.context['filter_mode'], 'upcoming')
                self.assertContains(response, 'Future Session')
                self.assertNotContains(response, 'Old Session')
                self.assertNotContains(
                    response,
                    'data-testid="events-past-section"',
                )

    def test_each_mode_renders_one_contextual_collection_heading(self):
        upcoming = self.client.get('/events')
        past = self.client.get('/events?filter=past')

        self.assertContains(
            upcoming,
            '<h2 class="sr-only" data-testid="events-collection-heading">'
            'Upcoming events</h2>',
            html=True,
        )
        self.assertContains(
            past,
            '<h2 class="sr-only" data-testid="events-collection-heading">'
            'Past events</h2>',
            html=True,
        )

    def test_empty_upcoming_uses_member_empty_state(self):
        Event.objects.all().delete()
        response = self.client.get('/events')
        self.assertContains(response, 'No upcoming events yet')


class TimelineTimezoneNoteTest(TestCase):
    def test_anonymous_list_and_calendar_explain_source_timezones(self):
        for url in ('/events', '/events/calendar'):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertContains(response, 'data-testid="events-timezone-note"')
                self.assertContains(
                    response, "Event times are shown in each event's timezone.",
                )
                self.assertContains(response, 'Sign in to use your timezone')

    def test_member_list_and_calendar_name_preferred_timezone_once(self):
        member = User.objects.create_user(
            email='timeline-timezone@example.com',
            password='pw',
            preferred_timezone='Europe/Berlin',
        )
        self.client.force_login(member)

        for url in ('/events', '/events/calendar'):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertContains(
                    response,
                    'Event times are shown in your timezone: Europe/Berlin.',
                )
                self.assertContains(response, 'Change timezone')

    def test_series_uses_authored_timezone_then_member_preference(self):
        series = EventSeries.objects.create(
            name='Timezone Series',
            slug='timezone-series-1382',
            timezone='America/New_York',
        )
        Event.objects.create(
            title='Timezone Session',
            slug='timezone-session-1382',
            start_datetime=_future_utc(3),
            timezone='America/New_York',
            status='upcoming',
            origin='studio',
            event_series=series,
            series_position=1,
        )
        anonymous = self.client.get(series.get_absolute_url())
        self.assertContains(
            anonymous, 'Event times are shown in America/New_York.',
        )

        member = User.objects.create_user(
            email='series-timezone@example.com',
            password='pw',
            preferred_timezone='Asia/Kolkata',
        )
        self.client.force_login(member)
        signed_in = self.client.get(series.get_absolute_url())
        self.assertContains(
            signed_in,
            'Event times are shown in your timezone: Asia/Kolkata.',
        )


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
        # Compact 24-hour labels from the view helper.
        self.assertContains(response, '09:00')


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

    def test_series_collapses_to_one_card_with_badge_count_and_next_time(self):
        response = self.client.get('/events')
        self.assertContains(response, 'data-testid="event-series-card"')
        self.assertContains(response, '>Series<')
        self.assertContains(response, '3 upcoming sessions')
        self.assertContains(response, '18:00')

    def test_series_card_does_not_list_every_session(self):
        response = self.client.get('/events')
        # The stable series title replaces repeated occurrence titles.
        self.assertContains(response, 'Build Club')
        self.assertNotContains(response, 'Build Club Session 0')
        self.assertNotContains(response, 'Build Club Session 1')
        self.assertNotContains(response, 'Build Club Session 2')
        # Exactly one series card for the three occurrences.
        self.assertEqual(
            response.content.decode().count('data-testid="event-series-card"'),
            1,
        )

    def test_recordingless_past_occurrence_stays_an_individual_event_row(self):
        past_occurrence = Event.objects.create(
            title='Build Club Finished Session',
            slug='build-club-finished-session-1382',
            start_datetime=timezone.now() - timedelta(days=2),
            end_datetime=timezone.now() - timedelta(days=2, hours=-1),
            timezone='UTC',
            status='completed',
            origin='studio',
            event_series=self.series,
            series_position=0,
            published=True,
        )

        response = self.client.get('/events?filter=past')

        self.assertContains(response, past_occurrence.title)
        self.assertContains(response, 'data-testid="past-event-card"')
        self.assertContains(response, f'href="{past_occurrence.get_absolute_url()}"')
        self.assertNotContains(response, 'data-testid="event-series-card"')
        self.assertNotContains(response, 'data-testid="past-card-recording-cta"')


class TimelineThumbnailTest(TestCase):
    def test_authored_cover_is_not_repeated_in_text_first_row(self):
        Event.objects.create(
            title='Covered Event',
            slug='covered-event-1382',
            start_datetime=_future_utc(2),
            timezone='UTC',
            status='upcoming',
            cover_image_url='https://cdn.aishippinglabs.com/events/cover.jpg',
        )
        response = self.client.get('/events')
        self.assertNotContains(response, 'data-testid="event-card-thumbnail"')
        self.assertNotContains(
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
