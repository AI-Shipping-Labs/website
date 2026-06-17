"""Tests for the social-proof attendee-count chip — issue #668.

Covers:

- ``Event.attendee_count`` property: prefers a ``_attendee_count``
  annotation when present, otherwise falls back to ``registration_count``.
- Event detail page: chip copy on upcoming events with 0, 1, and 2+
  registrations, and on past events with 0, 1, and 2+ registrations.
- Series page: per-card chip copy, and that the queryset is annotated
  so attendee counting does not generate a per-event ``COUNT(*)`` query.
"""

from datetime import time, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from events.models import Event, EventRegistration, EventSeries

User = get_user_model()


def _register_users(event, n):
    """Create ``n`` distinct users and register each for ``event``."""
    for i in range(n):
        email = f'user-{event.slug}-{i}@example.com'
        user = User.objects.create(email=email)
        EventRegistration.objects.create(event=event, user=user)


class EventAttendeeCountPropertyTest(TestCase):
    """``Event.attendee_count`` prefers an annotated value when present."""

    def test_falls_back_to_registration_count_when_not_annotated(self):
        event = Event.objects.create(
            title='Plain', slug='plain-evt',
            start_datetime=timezone.now() + timedelta(days=1),
            status='upcoming',
        )
        _register_users(event, 3)
        fresh = Event.objects.get(pk=event.pk)
        # Plain queryset — no annotation present.
        self.assertFalse(hasattr(fresh, '_attendee_count'))
        self.assertEqual(fresh.attendee_count, 3)

    def test_prefers_annotated_value(self):
        event = Event.objects.create(
            title='Annotated', slug='annotated-evt',
            start_datetime=timezone.now() + timedelta(days=1),
            status='upcoming',
        )
        _register_users(event, 2)
        # Simulate the annotation the series view sets, with a value
        # different from the real count to prove the annotation wins.
        event._attendee_count = 99
        self.assertEqual(event.attendee_count, 99)


class EventDetailAttendeeChipCopyTest(TestCase):
    """Chip copy variants on the public event detail page."""

    def _make_event(self, slug, status='upcoming'):
        if status == 'upcoming':
            start = timezone.now() + timedelta(days=7)
        else:
            start = timezone.now() - timedelta(days=7)
        return Event.objects.create(
            title=f'Event {slug}', slug=slug,
            start_datetime=start,
            status=status,
        )

    def test_upcoming_with_zero_shows_be_the_first(self):
        event = self._make_event('upcoming-zero', status='upcoming')
        response = self.client.get(event.get_absolute_url())
        self.assertContains(
            response, 'data-testid="event-attendee-count"',
        )
        self.assertContains(response, 'Be the first to sign up')
        self.assertNotContains(response, '0 people are going')

    def test_upcoming_with_one_uses_singular(self):
        event = self._make_event('upcoming-one', status='upcoming')
        _register_users(event, 1)
        response = self.client.get(event.get_absolute_url())
        self.assertContains(response, '1 person is going')
        self.assertNotContains(response, '1 people are going')

    def test_upcoming_with_many_uses_plural(self):
        event = self._make_event('upcoming-many', status='upcoming')
        _register_users(event, 5)
        response = self.client.get(event.get_absolute_url())
        self.assertContains(response, '5 people are going')

    def test_past_with_zero_hides_chip(self):
        event = self._make_event('past-zero', status='completed')
        response = self.client.get(event.get_absolute_url())
        # The chip element itself must not be in the DOM when a past
        # event has zero attendees — there is no social proof to show.
        self.assertNotContains(
            response, 'data-testid="event-attendee-count"',
        )

    def test_past_with_one_uses_singular_attended(self):
        event = self._make_event('past-one', status='completed')
        _register_users(event, 1)
        response = self.client.get(event.get_absolute_url())
        self.assertContains(response, '1 person attended')
        self.assertNotContains(response, '1 people attended')
        self.assertNotContains(response, '1 person is going')

    def test_past_with_many_uses_plural_attended(self):
        event = self._make_event('past-many', status='completed')
        _register_users(event, 12)
        response = self.client.get(event.get_absolute_url())
        self.assertContains(response, '12 people attended')
        self.assertNotContains(response, '12 people are going')

    def test_cancelled_with_attendees_uses_attended_copy(self):
        event = self._make_event('cancelled-evt', status='cancelled')
        _register_users(event, 3)
        response = self.client.get(event.get_absolute_url())
        self.assertContains(response, '3 people attended')


class EventSeriesAttendeeChipTest(TestCase):
    """Series page renders per-card chips with annotated counts."""

    @classmethod
    def setUpTestData(cls):
        cls.series = EventSeries.objects.create(
            name='Weekly Builds', slug='weekly-builds',
            start_time=time(18, 0),
        )

    def _add_event(self, slug, position, count=0, status='upcoming'):
        if status == 'upcoming':
            start = timezone.now() + timedelta(days=position)
        else:
            start = timezone.now() - timedelta(days=position)
        event = Event.objects.create(
            title=f'Session {position}', slug=slug,
            start_datetime=start,
            status=status,
            event_series=self.series, series_position=position,
            origin='studio',
        )
        if count:
            _register_users(event, count)
        return event

    def test_series_renders_chip_per_card_with_correct_copy(self):
        self._add_event('weekly-1', 1, count=0)
        self._add_event('weekly-2', 2, count=1)
        self._add_event('weekly-3', 3, count=5)

        response = self.client.get(self.series.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        # Three chip elements, one per card.
        self.assertEqual(
            body.count('data-testid="event-attendee-count"'), 3,
        )
        self.assertIn('Be the first to sign up', body)
        self.assertIn('1 person is going', body)
        self.assertIn('5 people are going', body)

    def test_series_view_does_not_n_plus_one_on_attendee_counts(self):
        """Adding more events to a series must not add ``COUNT(*)`` queries.

        Issue #668 acceptance: the view annotates
        ``Count('registrations')`` so attendee counting resolves in a
        single SELECT regardless of how many events are listed. If the
        annotation is dropped, each card's ``event.attendee_count``
        falls back to ``registration_count``, which fires one
        ``COUNT(*)`` query per card.
        """
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        # 3-event baseline.
        for i in range(1, 4):
            self._add_event(f'baseline-{i}', i, count=2)
        with CaptureQueriesContext(connection) as cap_3:
            response = self.client.get(
                self.series.get_absolute_url()
            )
            self.assertEqual(response.status_code, 200)
        baseline = len(cap_3.captured_queries)

        # Add 7 more events with registrations.
        for i in range(4, 11):
            self._add_event(f'extra-{i}', i, count=2)
        with CaptureQueriesContext(connection) as cap_10:
            response = self.client.get(
                self.series.get_absolute_url()
            )
            self.assertEqual(response.status_code, 200)
        scaled = len(cap_10.captured_queries)

        # If attendee counting were N+1, adding 7 more events would add
        # at least 7 follow-up COUNT(*) queries. Require the delta to
        # stay well below the number of new events to allow for any
        # other unrelated per-page query that happens to be added later.
        self.assertLess(
            scaled - baseline, 7,
            f'Query count scaled with event count: '
            f'3 events -> {baseline} queries, 10 events -> {scaled}. '
            f'Likely an N+1 on EventRegistration counting.',
        )
