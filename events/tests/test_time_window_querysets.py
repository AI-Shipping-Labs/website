from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from content.views.home import _get_upcoming_events
from events.models import Event, EventRegistration
from events.services.time_windows import (
    past_events_queryset,
    past_recording_events_queryset,
    upcoming_events_queryset,
)

User = get_user_model()


def _event(slug, *, now, start_offset, end_offset=None, **overrides):
    defaults = {
        'slug': slug,
        'title': slug.replace('-', ' ').title(),
        'start_datetime': now + start_offset,
        'end_datetime': now + end_offset if end_offset is not None else None,
        'status': 'upcoming',
    }
    defaults.update(overrides)
    return Event.objects.create(**defaults)


class EventTimeWindowQuerysetTest(TestCase):
    def setUp(self):
        self.now = timezone.now().replace(microsecond=0)

    def test_shared_helpers_classify_effective_windows(self):
        future = _event(
            'future-end',
            now=self.now,
            start_offset=timedelta(hours=1),
            end_offset=timedelta(hours=2),
        )
        null_end_live = _event(
            'null-end-live',
            now=self.now,
            start_offset=timedelta(minutes=-30),
        )
        completed_future = _event(
            'completed-future',
            now=self.now,
            start_offset=timedelta(hours=3),
            end_offset=timedelta(hours=4),
            status='completed',
        )
        past = _event(
            'past-end',
            now=self.now,
            start_offset=timedelta(hours=-3),
            end_offset=timedelta(minutes=-1),
            status='completed',
        )
        null_end_past = _event(
            'null-end-past',
            now=self.now,
            start_offset=timedelta(minutes=-61),
        )
        stale_upcoming_past = _event(
            'stale-upcoming-past',
            now=self.now,
            start_offset=timedelta(hours=-2),
            end_offset=timedelta(minutes=-1),
            status='upcoming',
        )
        draft = _event(
            'draft-future',
            now=self.now,
            start_offset=timedelta(hours=5),
            end_offset=timedelta(hours=6),
            status='draft',
        )
        cancelled = _event(
            'cancelled-future',
            now=self.now,
            start_offset=timedelta(hours=7),
            end_offset=timedelta(hours=8),
            status='cancelled',
        )

        upcoming_ids = set(
            upcoming_events_queryset(now=self.now).values_list('id', flat=True)
        )
        past_ids = set(
            past_events_queryset(now=self.now).values_list('id', flat=True)
        )

        self.assertEqual(
            upcoming_ids,
            {future.id, null_end_live.id},
        )
        self.assertEqual(
            past_ids,
            {past.id, null_end_past.id, stale_upcoming_past.id},
        )
        self.assertNotIn(draft.id, upcoming_ids | past_ids)
        self.assertNotIn(cancelled.id, upcoming_ids | past_ids)
        self.assertNotIn(completed_future.id, upcoming_ids | past_ids)

    def test_past_recording_helper_requires_published_non_empty_recording(self):
        recorded = _event(
            'recorded',
            now=self.now,
            start_offset=timedelta(days=-2),
            end_offset=timedelta(days=-2, hours=1),
            status='completed',
            published=True,
            recording_url='https://video.test/recorded',
        )
        _event(
            'unpublished-recorded',
            now=self.now,
            start_offset=timedelta(days=-3),
            end_offset=timedelta(days=-3, hours=1),
            status='completed',
            published=False,
            recording_url='https://video.test/unpublished',
        )
        _event(
            'empty-recording',
            now=self.now,
            start_offset=timedelta(days=-4),
            end_offset=timedelta(days=-4, hours=1),
            status='completed',
            published=True,
            recording_url='',
        )

        self.assertEqual(
            list(past_recording_events_queryset(now=self.now)),
            [recorded],
        )


class EventsListTimeWindowTest(TestCase):
    def setUp(self):
        self.now = timezone.now().replace(microsecond=0)
        self.future = _event(
            'listed-future',
            now=self.now,
            start_offset=timedelta(days=2),
            end_offset=timedelta(days=2, hours=1),
            title='Listed Future',
        )
        self.completed_future = _event(
            'listed-completed-future',
            now=self.now,
            start_offset=timedelta(days=3),
            end_offset=timedelta(days=3, hours=1),
            status='completed',
            title='Listed Completed Future',
        )
        self.past_recorded = _event(
            'listed-recorded',
            now=self.now,
            start_offset=timedelta(days=-2),
            end_offset=timedelta(days=-2, hours=1),
            status='completed',
            title='Listed Recorded',
            recording_url='https://video.test/listed',
            tags=['agents'],
        )
        self.past_without_recording = _event(
            'listed-no-recording',
            now=self.now,
            start_offset=timedelta(days=-3),
            end_offset=timedelta(days=-3, hours=1),
            status='completed',
            title='Listed No Recording',
        )
        self.stale_upcoming = _event(
            'listed-stale-upcoming',
            now=self.now,
            start_offset=timedelta(hours=-2),
            end_offset=timedelta(minutes=-1),
            status='upcoming',
            title='Listed Stale Upcoming',
            recording_url='https://video.test/stale',
            tags=['agents'],
        )
        self.draft = _event(
            'listed-draft',
            now=self.now,
            start_offset=timedelta(days=4),
            end_offset=timedelta(days=4, hours=1),
            status='draft',
            title='Listed Draft',
            recording_url='https://video.test/draft',
        )
        self.cancelled = _event(
            'listed-cancelled',
            now=self.now,
            start_offset=timedelta(days=5),
            end_offset=timedelta(days=5, hours=1),
            status='cancelled',
            title='Listed Cancelled',
            recording_url='https://video.test/cancelled',
        )

    def test_all_filter_preserves_upcoming_and_past_contexts(self):
        response = self.client.get('/events?filter=all')

        upcoming_ids = {e.id for e in response.context['upcoming_events']}
        past_ids = {e.id for e in response.context['past_events']}

        self.assertEqual(upcoming_ids, {self.future.id})
        self.assertEqual(
            past_ids,
            {
                self.past_recorded.id,
                self.past_without_recording.id,
                self.stale_upcoming.id,
            },
        )
        self.assertTrue(response.context['show_upcoming'])
        self.assertTrue(response.context['show_past'])
        self.assertIn('upcoming_rows', response.context)
        self.assertIn('all_past_tags', response.context)
        self.assertNotIn(self.completed_future.id, upcoming_ids | past_ids)
        self.assertNotIn(self.draft.id, upcoming_ids | past_ids)
        self.assertNotIn(self.cancelled.id, upcoming_ids | past_ids)

    def test_upcoming_filter_uses_effective_future_bucket(self):
        response = self.client.get('/events?filter=upcoming')

        upcoming_ids = {e.id for e in response.context['upcoming_events']}
        past_ids = {e.id for e in response.context['past_events']}

        self.assertEqual(upcoming_ids, {self.future.id})
        self.assertEqual(past_ids, set())
        self.assertTrue(response.context['show_upcoming'])
        self.assertFalse(response.context['show_past'])

    def test_past_filter_requires_recording_and_preserves_tag_filter(self):
        response = self.client.get('/events?filter=past&tag=agents')

        past_ids = {e.id for e in response.context['past_events']}

        self.assertEqual(past_ids, {self.past_recorded.id, self.stale_upcoming.id})
        self.assertFalse(response.context['show_upcoming'])
        self.assertTrue(response.context['show_past'])
        self.assertEqual(response.context['selected_tags'], ['agents'])
        self.assertEqual(response.context['current_tag'], 'agents')
        self.assertIn('agents', response.context['all_past_tags'])
        self.assertIsNotNone(response.context['page_obj'])
        self.assertFalse(response.context['is_paginated'])


class DashboardRegisteredUpcomingEventsTest(TestCase):
    def test_returns_next_three_registered_eligible_future_events(self):
        now = timezone.now().replace(microsecond=0)
        user = User.objects.create_user(email='dash1022@test.com', password='x')

        eligible_events = [
            _event(
                f'dash-eligible-{index}',
                now=now,
                start_offset=timedelta(days=index + 1),
                end_offset=timedelta(days=index + 1, hours=1),
                title=f'Dash Eligible {index}',
            )
            for index in range(4)
        ]
        completed_future = _event(
            'dash-completed-future',
            now=now,
            start_offset=timedelta(hours=12),
            end_offset=timedelta(hours=13),
            status='completed',
            title='Dash Completed Future',
        )
        draft = _event(
            'dash-draft',
            now=now,
            start_offset=timedelta(hours=2),
            end_offset=timedelta(hours=3),
            status='draft',
            title='Dash Draft',
        )
        cancelled = _event(
            'dash-cancelled',
            now=now,
            start_offset=timedelta(hours=3),
            end_offset=timedelta(hours=4),
            status='cancelled',
            title='Dash Cancelled',
        )
        past = _event(
            'dash-past',
            now=now,
            start_offset=timedelta(days=-1),
            end_offset=timedelta(days=-1, hours=1),
            status='completed',
            title='Dash Past',
        )

        for event in [*eligible_events, completed_future, draft, cancelled, past]:
            EventRegistration.objects.create(user=user, event=event)

        results = _get_upcoming_events(user)

        self.assertEqual(
            [event.title for event in results],
            ['Dash Eligible 0', 'Dash Eligible 1', 'Dash Eligible 2'],
        )
        self.assertNotIn(completed_future.id, [event.id for event in results])
