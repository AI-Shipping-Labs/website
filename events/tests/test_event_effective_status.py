"""A finished event must not report ``upcoming`` before the daily cron runs.

The ``complete-finished-events`` task flips the stored ``status`` only once
a day (04:00 UTC). Between an event's end and that run, the staff API and
the Studio state panel used to return the stale stored ``upcoming`` value
(event 77 reported ``upcoming`` for ~11 hours after it ended). Read
surfaces now use ``Event.effective_status`` / ``ended_upcoming_events_q``.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import Token
from events.models import Event
from events.models.event import ended_upcoming_events_q

User = get_user_model()


def _event(slug, status='upcoming', start_offset=None, end_offset=None):
    now = timezone.now()
    start = now + (start_offset if start_offset is not None else timedelta(days=1))
    end = now + end_offset if end_offset is not None else None
    return Event.objects.create(
        title=slug.replace('-', ' ').title(),
        slug=slug,
        start_datetime=start,
        end_datetime=end,
        status=status,
        published=True,
    )


class EffectiveStatusModelTest(TestCase):
    def test_ended_upcoming_row_reports_completed(self):
        event = _event(
            'ended-explicit',
            start_offset=timedelta(hours=-2),
            end_offset=timedelta(seconds=-1),
        )
        self.assertEqual(event.status, 'upcoming')
        self.assertEqual(event.effective_status, 'completed')

    def test_ended_upcoming_row_without_end_uses_one_hour_fallback(self):
        ended = _event('ended-implicit', start_offset=timedelta(minutes=-61))
        running = _event('running-implicit', start_offset=timedelta(minutes=-59))
        self.assertEqual(ended.effective_status, 'completed')
        self.assertEqual(running.effective_status, 'upcoming')

    def test_future_and_other_statuses_unchanged(self):
        future = _event('future', end_offset=timedelta(days=1, hours=1))
        draft = _event('old-draft', status='draft', start_offset=timedelta(days=-3))
        cancelled = _event(
            'old-cancelled', status='cancelled', start_offset=timedelta(days=-3),
        )
        self.assertEqual(future.effective_status, 'upcoming')
        self.assertEqual(draft.effective_status, 'draft')
        self.assertEqual(cancelled.effective_status, 'cancelled')

    def test_query_matches_property(self):
        _event(
            'q-ended', start_offset=timedelta(hours=-2),
            end_offset=timedelta(minutes=-5),
        )
        _event('q-ended-implicit', start_offset=timedelta(hours=-2))
        _event('q-future', end_offset=timedelta(days=1, hours=1))
        _event('q-draft', status='draft', start_offset=timedelta(days=-3))
        _event('q-done', status='completed', start_offset=timedelta(days=-3))
        matched = set(
            Event.objects.filter(ended_upcoming_events_q())
            .values_list('slug', flat=True),
        )
        self.assertEqual(matched, {'q-ended', 'q-ended-implicit'})
        for event in Event.objects.all():
            self.assertEqual(
                event.slug in matched,
                event.status == 'upcoming' and event.effective_status == 'completed',
                event.slug,
            )


class EffectiveStatusSurfacesTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff-effective-status@test.com',
            password='pw',
            is_staff=True,
        )
        cls.token = Token.objects.create(user=cls.staff, name='effective-status')
        cls.ended = _event(
            'ended-stale-upcoming',
            start_offset=timedelta(hours=-12),
            end_offset=timedelta(hours=-11),
        )
        cls.future = _event('still-upcoming', end_offset=timedelta(days=1, hours=1))
        cls.completed = _event(
            'already-completed', status='completed',
            start_offset=timedelta(days=-3),
        )

    def _api_get(self, path):
        response = self.client.get(
            path, HTTP_AUTHORIZATION=f'Token {self.token.key}',
        )
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def test_api_detail_reports_completed_for_ended_event(self):
        body = self._api_get(f'/api/events/{self.ended.slug}')
        self.assertEqual(body['status'], 'completed')
        self.ended.refresh_from_db()
        self.assertEqual(self.ended.status, 'upcoming')

    def test_api_detail_future_event_stays_upcoming(self):
        body = self._api_get(f'/api/events/{self.future.slug}')
        self.assertEqual(body['status'], 'upcoming')

    def _list_slugs(self, status):
        body = self._api_get(f'/api/events?status={status}')
        return {item['slug'] for item in body['events']}

    def test_api_list_status_filters_are_time_derived(self):
        upcoming = self._list_slugs('upcoming')
        completed = self._list_slugs('completed')
        self.assertIn(self.future.slug, upcoming)
        self.assertNotIn(self.ended.slug, upcoming)
        self.assertIn(self.ended.slug, completed)
        self.assertIn(self.completed.slug, completed)
        self.assertNotIn(self.future.slug, completed)

    def test_studio_state_panel_shows_completed_for_ended_event(self):
        self.client.force_login(self.staff)
        response = self.client.get(f'/studio/events/{self.ended.pk}/edit')
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        panel = content[content.index('data-testid="event-state-panel"'):]
        panel = panel[:panel.index('</dl>')]
        self.assertIn('Completed', panel)
        self.assertNotIn('Upcoming', panel)
