"""Tests for the shared content sync enqueue service.

Ported to the package durable dispatcher (A2.3): enqueueing creates the
queued marker row and one package dispatch intent; workers pick the intent
up and run the package engine. Task naming and inline fallbacks are owned
by the package now, so those legacy contract tests were retired with the
``django_q`` path.
"""

import uuid
from unittest.mock import MagicMock, patch

from community_base.content_sync.models import SyncLog as PackageSyncLog
from django.test import TestCase

from integrations.models import ContentSource
from integrations.services.content_sync_queue import (
    enqueue_content_sync,
    enqueue_content_syncs,
)


class ContentSyncQueueServiceTest(TestCase):
    def setUp(self):
        self.source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            webhook_secret='secret',
        )

    @patch(
        'integrations.services.content_sync_queue.package_queue_source_sync',
        return_value=(MagicMock(), True),
    )
    def test_enqueue_returns_structured_queued_result(self, mock_queue):
        result = enqueue_content_sync(self.source)

        self.assertTrue(result.ok)
        self.assertTrue(result.queued)
        self.assertFalse(result.ran_inline)
        self.assertEqual(result.source, self.source)
        self.assertEqual(mock_queue.call_count, 1)

    @patch(
        'integrations.services.content_sync_queue.package_queue_source_sync',
        return_value=(MagicMock(), True),
    )
    def test_enqueue_marks_source_queued_when_requested(self, mock_queue):
        enqueue_content_sync(self.source)

        marker = PackageSyncLog.objects.get(
            source_id=self.source.pk, status='queued',
        )
        self.assertIsNotNone(marker.pk)
        self.assertTrue(mock_queue.call_args.kwargs.get('batch_id') is None)

    @patch(
        'integrations.services.content_sync_queue.package_queue_source_sync',
        return_value=(MagicMock(), True),
    )
    def test_enqueue_uses_batch_id_for_queued_log(self, mock_queue):
        batch_id = uuid.uuid4()

        result = enqueue_content_sync(self.source, batch_id=batch_id)

        self.assertEqual(result.batch_id, batch_id)
        marker = PackageSyncLog.objects.get(
            source_id=self.source.pk, status='queued',
        )
        self.assertEqual(marker.batch_id, batch_id)
        self.assertEqual(mock_queue.call_args.kwargs['batch_id'], batch_id)

    @patch(
        'integrations.services.content_sync_queue.package_queue_source_sync',
        return_value=(MagicMock(), True),
    )
    def test_mark_queued_false_does_not_create_queued_state(self, mock_queue):
        enqueue_content_sync(self.source, mark_queued=False)

        self.assertFalse(
            PackageSyncLog.objects.filter(
                source_id=self.source.pk,
            ).exists(),
        )
        self.assertEqual(mock_queue.call_count, 1)

    @patch(
        'integrations.services.content_sync_queue.package_queue_source_sync',
        return_value=(MagicMock(), True),
    )
    def test_force_true_forwarded_to_dispatcher(self, mock_queue):
        enqueue_content_sync(self.source, force=True)

        self.assertTrue(mock_queue.call_args.kwargs['force'])

    @patch(
        'integrations.services.content_sync_queue.package_queue_source_sync',
        side_effect=Exception('queue error'),
    )
    def test_enqueue_error_returns_failure_with_queued_marker(
        self,
        mock_queue,
    ):
        result = enqueue_content_sync(self.source)

        self.assertFalse(result.ok)
        self.assertFalse(result.queued)
        self.assertFalse(result.ran_inline)
        self.assertEqual(result.error, 'queue error')
        # The marker row and the dispatch intent share one transaction, so
        # a dispatcher failure rolls both back: nothing claims a queued
        # state that never landed in the queue.
        self.assertFalse(
            PackageSyncLog.objects.filter(
                source_id=self.source.pk, status='queued',
            ).exists(),
        )


class ContentSyncQueueBulkServiceTest(TestCase):
    @patch(
        'integrations.services.content_sync_queue.package_queue_source_sync',
        return_value=(MagicMock(), True),
    )
    def test_bulk_enqueue_returns_one_result_per_source(self, mock_queue):
        source_a = ContentSource.objects.create(
            repo_name='Org/a', webhook_secret='secret-a',
        )
        source_b = ContentSource.objects.create(
            repo_name='Org/b', webhook_secret='secret-b',
        )
        batch_id = uuid.uuid4()

        results = enqueue_content_syncs(
            [source_a, source_b],
            batch_id=batch_id,
        )

        self.assertEqual(len(results), 2)
        self.assertTrue(all(result.ok for result in results))
        self.assertEqual(mock_queue.call_count, 2)
        self.assertEqual(
            PackageSyncLog.objects.filter(
                batch_id=batch_id, status='queued',
            ).count(),
            2,
        )
