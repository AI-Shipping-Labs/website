from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from events.services.zoom_lifecycle import (
    maybe_sync_zoom_meeting,
    sync_changed_zoom_occurrences,
)
from integrations.services.zoom import ZoomAPIError


class SyncChangedZoomOccurrencesTest(SimpleTestCase):
    @patch('events.services.zoom_lifecycle.sync_or_delete_zoom_meeting')
    def test_deduplicates_rows_skips_direct_event_and_collects_errors(self, sync):
        first = SimpleNamespace(pk=1)
        second = SimpleNamespace(pk=2)
        old_first = SimpleNamespace(pk=1)
        old_second = SimpleNamespace(pk=2)
        sync.side_effect = ['provider failed']

        errors = sync_changed_zoom_occurrences(
            [
                (first, old_first),
                (first, old_first),
                (second, old_second),
            ],
            skip_event_ids={2},
        )

        sync.assert_called_once_with(first, old_first)
        self.assertEqual(
            errors,
            [{'event_id': 1, 'zoom_error': 'provider failed'}],
        )


class ZoomLifecycleFailureTest(SimpleTestCase):
    def _event(self, **overrides):
        values = {
            'pk': 42,
            'slug': 'rescheduled-event',
            'platform': 'zoom',
            'zoom_meeting_id': '83220656366',
            'status': 'upcoming',
            'title': 'Rescheduled event',
            'start_datetime': datetime(
                2026, 8, 3, 8, 0,
                tzinfo=UTC,
            ),
            'end_datetime': datetime(
                2026, 8, 3, 10, 0,
                tzinfo=UTC,
            ),
            'timezone': 'Europe/Berlin',
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    @patch('events.services.zoom_lifecycle.update_meeting')
    def test_failure_is_structured_logged_and_explains_local_state(self, update):
        old_event = self._event(title='Old title')
        event = self._event()
        update.side_effect = ZoomAPIError(
            'Zoom PATCH failed',
            status_code=400,
            response_data={
                'code': 300,
                'message': 'Invalid field; join_url=https://zoom.us/j/private',
                'authorization': 'Bearer secret',
            },
        )

        with self.assertLogs('events.services.zoom_lifecycle', level='ERROR') as logs:
            failure = maybe_sync_zoom_meeting(event, old_event)

        self.assertEqual(failure['operation'], 'update_meeting')
        self.assertEqual(failure['http_status'], 400)
        self.assertEqual(failure['provider_code'], 300)
        self.assertEqual(
            failure['provider_message'],
            'Invalid field; join_url=[redacted]',
        )
        self.assertIn('local event was saved', str(failure))
        self.assertIn('/api/events/rescheduled-event/sync-zoom', str(failure))
        logged = '\n'.join(logs.output)
        self.assertIn('event_id=42', logged)
        self.assertIn('zoom_meeting_id=83220656366', logged)
        self.assertNotIn('zoom.us', logged)
        self.assertNotIn('Bearer secret', logged)
