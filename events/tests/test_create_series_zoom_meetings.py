"""Tests for the one-click series Zoom-meeting creation task (issue #859).

The Zoom API is mocked at ``events.tasks.create_series_zoom_meetings.create_meeting``
so no live credentials are needed. These cover the worker's eligibility
selection, idempotency (skip existing), ineligible-occurrence skipping, and
partial-failure resilience (one Zoom error does not abort the batch).
"""

from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from events.models import Event, EventSeries
from events.tasks.create_series_zoom_meetings import (
    create_series_zoom_meetings,
    eligible_occurrence_count,
)
from integrations.services.zoom import ZoomAPIError


def _meeting(meeting_id):
    return {
        'meeting_id': str(meeting_id),
        'join_url': f'https://zoom.us/j/{meeting_id}',
    }


class CreateSeriesZoomMeetingsTaskTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.series = EventSeries.objects.create(
            name='Weekly Build', slug='weekly-build',
            start_time='18:00', timezone='Europe/Berlin',
        )
        now = timezone.now()
        cls.future_a = Event.objects.create(
            title='Session 1', slug='wb-1', platform='zoom',
            start_datetime=now + timedelta(days=7),
            end_datetime=now + timedelta(days=7, hours=1),
            timezone='Europe/Berlin', status='upcoming',
            event_series=cls.series, series_position=1,
        )
        cls.future_b = Event.objects.create(
            title='Session 2', slug='wb-2', platform='zoom',
            start_datetime=now + timedelta(days=14),
            end_datetime=now + timedelta(days=14, hours=1),
            timezone='Europe/Berlin', status='upcoming',
            event_series=cls.series, series_position=2,
        )

    def test_creates_meetings_for_eligible_occurrences(self):
        with patch(
            'events.tasks.create_series_zoom_meetings.create_meeting',
        ) as mock_create:
            mock_create.side_effect = [_meeting('1001'), _meeting('1002')]
            summary = create_series_zoom_meetings(self.series.pk)

        self.assertEqual(len(summary['created']), 2)
        self.assertEqual(summary['skipped_existing'], 0)
        self.assertEqual(summary['skipped_ineligible'], 0)
        self.assertEqual(summary['failed'], [])

        self.future_a.refresh_from_db()
        self.future_b.refresh_from_db()
        self.assertTrue(self.future_a.zoom_meeting_id)
        self.assertTrue(self.future_a.zoom_join_url)
        self.assertTrue(self.future_b.zoom_meeting_id)

    def test_summary_persisted_on_series(self):
        with patch(
            'events.tasks.create_series_zoom_meetings.create_meeting',
        ) as mock_create:
            mock_create.side_effect = [_meeting('1'), _meeting('2')]
            create_series_zoom_meetings(self.series.pk)

        self.series.refresh_from_db()
        self.assertIsNotNone(self.series.zoom_meetings_last_run)
        self.assertEqual(len(self.series.zoom_meetings_last_run['created']), 2)

    def test_existing_meeting_is_skipped_not_recreated(self):
        self.future_a.zoom_meeting_id = 'live-99'
        self.future_a.zoom_join_url = 'https://zoom.us/j/live-99'
        self.future_a.save(update_fields=['zoom_meeting_id', 'zoom_join_url'])

        with patch(
            'events.tasks.create_series_zoom_meetings.create_meeting',
        ) as mock_create:
            mock_create.return_value = _meeting('2002')
            summary = create_series_zoom_meetings(self.series.pk)

        # Only the second occurrence got a fresh meeting.
        self.assertEqual(mock_create.call_count, 1)
        self.assertEqual(summary['skipped_existing'], 1)
        self.assertEqual(len(summary['created']), 1)

        # The pre-existing meeting id is untouched.
        self.future_a.refresh_from_db()
        self.assertEqual(self.future_a.zoom_meeting_id, 'live-99')

    def test_one_failure_does_not_abort_batch(self):
        with patch(
            'events.tasks.create_series_zoom_meetings.create_meeting',
        ) as mock_create:
            # First eligible succeeds; second raises a Zoom 429.
            mock_create.side_effect = [
                _meeting('3001'),
                ZoomAPIError('Too Many Requests', status_code=429),
            ]
            with self.assertLogs(
                'events.tasks.create_series_zoom_meetings', level='ERROR',
            ):
                summary = create_series_zoom_meetings(self.series.pk)

        self.assertEqual(len(summary['created']), 1)
        self.assertEqual(len(summary['failed']), 1)
        failure = summary['failed'][0]
        self.assertIn('Too Many Requests', failure['error'])

        # Exactly one occurrence got a meeting; the failed one (whichever the
        # iteration hit second) did not — and the failure record names it.
        self.future_a.refresh_from_db()
        self.future_b.refresh_from_db()
        with_meeting = [
            e for e in (self.future_a, self.future_b) if e.zoom_meeting_id
        ]
        without_meeting = [
            e for e in (self.future_a, self.future_b) if not e.zoom_meeting_id
        ]
        self.assertEqual(len(with_meeting), 1)
        self.assertEqual(len(without_meeting), 1)
        self.assertEqual(failure['event_id'], without_meeting[0].pk)

    def test_missing_series_returns_skipped(self):
        with patch(
            'events.tasks.create_series_zoom_meetings.create_meeting',
        ) as mock_create:
            result = create_series_zoom_meetings(999999)
        mock_create.assert_not_called()
        self.assertEqual(result['status'], 'skipped')
        self.assertEqual(result['reason'], 'missing_series')


class EligibilitySelectionTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.series = EventSeries.objects.create(
            name='Mixed', slug='mixed',
            start_time='18:00', timezone='Europe/Berlin',
        )
        now = timezone.now()
        # Eligible: future zoom, no meeting.
        cls.eligible = Event.objects.create(
            title='Future Zoom', slug='mx-future', platform='zoom',
            start_datetime=now + timedelta(days=7),
            end_datetime=now + timedelta(days=7, hours=1),
            timezone='Europe/Berlin', status='upcoming',
            event_series=cls.series, series_position=1,
        )
        # Past: ineligible.
        cls.past = Event.objects.create(
            title='Past Zoom', slug='mx-past', platform='zoom',
            start_datetime=now - timedelta(days=7),
            end_datetime=now - timedelta(days=7) + timedelta(hours=1),
            timezone='Europe/Berlin', status='completed',
            event_series=cls.series, series_position=2,
        )
        # Custom platform: ineligible even though future.
        cls.custom = Event.objects.create(
            title='Future Custom', slug='mx-custom', platform='custom',
            start_datetime=now + timedelta(days=14),
            end_datetime=now + timedelta(days=14, hours=1),
            timezone='Europe/Berlin', status='upcoming',
            event_series=cls.series, series_position=3,
        )
        # Draft zoom: ineligible (is_upcoming is False for drafts).
        cls.draft = Event.objects.create(
            title='Draft Zoom', slug='mx-draft', platform='zoom',
            start_datetime=now + timedelta(days=21),
            end_datetime=now + timedelta(days=21, hours=1),
            timezone='Europe/Berlin', status='draft',
            event_series=cls.series, series_position=4,
        )

    def test_only_future_zoom_without_meeting_is_eligible(self):
        self.assertEqual(eligible_occurrence_count(self.series), 1)

    def test_ineligible_occurrences_counted_as_skipped(self):
        with patch(
            'events.tasks.create_series_zoom_meetings.create_meeting',
        ) as mock_create:
            mock_create.return_value = _meeting('5001')
            summary = create_series_zoom_meetings(self.series.pk)

        self.assertEqual(len(summary['created']), 1)
        self.assertEqual(summary['skipped_existing'], 0)
        # past + custom + draft = 3 ineligible.
        self.assertEqual(summary['skipped_ineligible'], 3)

        self.past.refresh_from_db()
        self.custom.refresh_from_db()
        self.draft.refresh_from_db()
        self.assertEqual(self.past.zoom_meeting_id, '')
        self.assertEqual(self.custom.zoom_meeting_id, '')
        self.assertEqual(self.draft.zoom_meeting_id, '')
