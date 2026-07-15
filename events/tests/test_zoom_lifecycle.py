from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from events.services.zoom_lifecycle import sync_changed_zoom_occurrences


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
