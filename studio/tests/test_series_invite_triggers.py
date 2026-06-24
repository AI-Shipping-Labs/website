"""Studio edit-view triggers for series subscriber calendar invites (#869).

The Studio occurrence edit path must:
- On a status flip INTO ``cancelled`` for a series occurrence, bump the
  occurrence's ``ics_sequence`` and enqueue the series cancellation
  fan-out (METHOD:CANCEL to subscribers).
- On a meaningful start-time change for a series occurrence, enqueue the
  series update fan-out (METHOD:REQUEST to subscribers) in addition to the
  per-event reschedule notice.
- NOT enqueue a cancellation when the occurrence does not belong to a
  series, or when the status was already cancelled (no transition).
"""

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from events.models import Event, EventSeries
from tests.fixtures import StaffUserMixin

User = get_user_model()


def _make_series():
    return EventSeries.objects.create(
        name='Weekly Office Hours',
        slug='woh-triggers',
        start_time=timezone.now().time(),
        timezone='UTC',
    )


class StudioSeriesCancellationTriggerTest(StaffUserMixin, TestCase):
    """Cancelling a series occurrence via Studio notifies subscribers."""

    def setUp(self):
        self.client.login(**self.staff_credentials)
        self.series = _make_series()
        start = timezone.now() + timedelta(days=14)
        self.event = Event.objects.create(
            title='Session 1',
            slug='woh-trig-1',
            start_datetime=start,
            end_datetime=start + timedelta(hours=1),
            status='upcoming',
            timezone='UTC',
            origin='studio',
            ics_sequence=0,
            event_series=self.series,
        )
        self._date = start.strftime('%d/%m/%Y')
        self._time = start.strftime('%H:%M')

    def _post_edit(self, **overrides):
        data = {
            'title': 'Session 1',
            'slug': 'woh-trig-1',
            'event_date': self._date,
            'event_time': self._time,
            'duration_hours': '1',
            'timezone': 'UTC',
            'status': 'upcoming',
            'required_level': '0',
        }
        data.update(overrides)
        return self.client.post(
            f'/studio/events/{self.event.pk}/edit', data, follow=True,
        )

    @patch('events.tasks.notify_series_invite.enqueue_series_cancellation')
    def test_cancel_bumps_sequence_and_enqueues(self, mock_cancel):
        self._post_edit(status='cancelled')

        self.event.refresh_from_db()
        self.assertEqual(self.event.status, 'cancelled')
        # SEQUENCE bumped so the CANCEL outranks the prior invite.
        self.assertEqual(self.event.ics_sequence, 1)
        mock_cancel.assert_called_once_with(self.event.pk)

    @patch('events.tasks.notify_series_invite.enqueue_series_cancellation')
    def test_no_enqueue_when_already_cancelled(self, mock_cancel):
        self.event.status = 'cancelled'
        self.event.save(update_fields=['status'])

        self._post_edit(status='cancelled')

        mock_cancel.assert_not_called()

    @patch('events.tasks.notify_series_invite.enqueue_series_cancellation')
    def test_no_enqueue_for_non_series_event(self, mock_cancel):
        standalone = Event.objects.create(
            title='Standalone',
            slug='standalone-trig',
            start_datetime=timezone.now() + timedelta(days=10),
            end_datetime=timezone.now() + timedelta(days=10, hours=1),
            status='upcoming',
            timezone='UTC',
            origin='studio',
            ics_sequence=0,
        )
        self.client.post(
            f'/studio/events/{standalone.pk}/edit',
            {
                'title': 'Standalone',
                'slug': 'standalone-trig',
                'event_date': (
                    standalone.start_datetime.strftime('%d/%m/%Y')
                ),
                'event_time': standalone.start_datetime.strftime('%H:%M'),
                'duration_hours': '1',
                'timezone': 'UTC',
                'status': 'cancelled',
                'required_level': '0',
            },
            follow=True,
        )
        mock_cancel.assert_not_called()


class StudioSeriesRescheduleTriggerTest(StaffUserMixin, TestCase):
    """A time change on a series occurrence enqueues the series update."""

    def setUp(self):
        self.client.login(**self.staff_credentials)
        self.series = _make_series()
        start = timezone.now() + timedelta(days=14)
        self.event = Event.objects.create(
            title='Session 1',
            slug='woh-resched-1',
            start_datetime=start,
            end_datetime=start + timedelta(hours=1),
            status='upcoming',
            timezone='UTC',
            origin='studio',
            ics_sequence=0,
            event_series=self.series,
        )

    @patch('events.tasks.notify_series_invite.enqueue_series_update')
    @patch('studio.views.events.enqueue_reschedule_notice')
    def test_time_change_enqueues_series_update(self, mock_resched, mock_update):
        new_start = self.event.start_datetime + timedelta(days=7)
        self.client.post(
            f'/studio/events/{self.event.pk}/edit',
            {
                'title': 'Session 1',
                'slug': 'woh-resched-1',
                'event_date': new_start.strftime('%d/%m/%Y'),
                'event_time': new_start.strftime('%H:%M'),
                'duration_hours': '1',
                'timezone': 'UTC',
                'status': 'upcoming',
                'required_level': '0',
            },
            follow=True,
        )
        # Issue #1071: the reschedule path now threads the changed
        # occurrence's old start (ISO string) so the series-update email can
        # name the moved session and show old -> new.
        mock_update.assert_called_once()
        self.assertEqual(mock_update.call_args.args[0], self.event.pk)
        self.assertIn('old_start_iso', mock_update.call_args.kwargs)
        self.assertTrue(mock_update.call_args.kwargs['old_start_iso'])
