"""Tests for the shared content sync enqueue service."""

import uuid
from unittest.mock import patch

from django.test import TestCase

from integrations.models import ContentSource, SyncLog
from integrations.services.content_sync_queue import (
    SYNC_TASK_PATH,
    enqueue_content_sync,
    enqueue_content_syncs,
)
from jobs.tasks import build_task_name


class ContentSyncQueueServiceTest(TestCase):
    def setUp(self):
        self.source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
        )

    @patch('django_q.tasks.async_task', return_value='task-id')
    def test_async_enqueue_returns_structured_queued_result(self, mock_async):
        result = enqueue_content_sync(self.source)

        self.assertTrue(result.ok)
        self.assertTrue(result.queued)
        self.assertFalse(result.ran_inline)
        self.assertEqual(result.source, self.source)
        self.assertEqual(result.task_id, 'task-id')
        mock_async.assert_called_once_with(
            SYNC_TASK_PATH,
            self.source,
            force=False,
            task_name=build_task_name(
                'Sync content source',
                self.source.repo_name,
                'content sync queue',
            ),
        )

    @patch('django_q.tasks.async_task', return_value='task-id')
    def test_async_enqueue_uses_call_site_source_in_task_name(self, mock_async):
        enqueue_content_sync(self.source, task_source='Studio sync dashboard')

        self.assertEqual(
            mock_async.call_args.kwargs['task_name'],
            build_task_name(
                'Sync content source',
                self.source.repo_name,
                'Studio sync dashboard',
            ),
        )

    @patch('django_q.tasks.async_task')
    def test_async_enqueue_marks_source_queued_when_requested(self, mock_async):
        enqueue_content_sync(self.source)

        self.source.refresh_from_db()
        self.assertEqual(self.source.last_sync_status, 'queued')
        log = SyncLog.objects.get(source=self.source)
        self.assertEqual(log.status, 'queued')

    @patch('integrations.services.content_sync_queue._enqueue_async_task')
    def test_async_enqueue_marks_queued_before_dispatch(self, mock_enqueue):
        """Issue #556: workers must see queued state before pickup."""
        observed = {}

        def observe(source, **kwargs):
            source.refresh_from_db()
            observed['status'] = source.last_sync_status
            observed['queued_log_exists'] = SyncLog.objects.filter(
                source=source,
                status='queued',
            ).exists()
            return 'task-id'

        mock_enqueue.side_effect = observe

        enqueue_content_sync(self.source)

        self.assertEqual(observed['status'], 'queued')
        self.assertTrue(observed['queued_log_exists'])

    @patch('django_q.tasks.async_task')
    def test_async_enqueue_uses_batch_id_for_queued_log(self, mock_async):
        batch_id = uuid.uuid4()

        result = enqueue_content_sync(self.source, batch_id=batch_id)

        self.assertEqual(result.batch_id, batch_id)
        log = SyncLog.objects.get(source=self.source)
        self.assertEqual(log.batch_id, batch_id)
        self.assertEqual(mock_async.call_args.kwargs['batch_id'], batch_id)

    @patch('django_q.tasks.async_task')
    def test_mark_queued_false_does_not_create_queued_state(self, mock_async):
        enqueue_content_sync(self.source, mark_queued=False)

        self.source.refresh_from_db()
        self.assertIsNone(self.source.last_sync_status)
        self.assertFalse(SyncLog.objects.filter(source=self.source).exists())

    @patch('django_q.tasks.async_task')
    def test_force_true_forwarded_to_async_task(self, mock_async):
        enqueue_content_sync(self.source, force=True)

        self.assertTrue(mock_async.call_args.kwargs['force'])

    @patch(
        'integrations.services.content_sync_queue.sync_content_source',
        return_value=None,
    )
    @patch(
        'integrations.services.content_sync_queue._enqueue_async_task',
        side_effect=ImportError('django-q unavailable'),
    )
    def test_import_error_fallback_runs_inline(self, mock_enqueue, mock_sync):
        result = enqueue_content_sync(self.source, force=True)

        self.assertTrue(result.ok)
        self.assertFalse(result.queued)
        self.assertTrue(result.ran_inline)
        mock_sync.assert_called_once_with(
            self.source,
            batch_id=None,
            force=True,
        )
        self.source.refresh_from_db()
        self.assertIsNone(self.source.last_sync_status)
        self.assertFalse(SyncLog.objects.filter(source=self.source).exists())

    @patch(
        'integrations.services.content_sync_queue.sync_content_source',
        side_effect=Exception('inline sync error'),
    )
    @patch(
        'integrations.services.content_sync_queue._enqueue_async_task',
        side_effect=ImportError('django-q unavailable'),
    )
    def test_import_error_fallback_sync_error_returns_failed_result(
        self,
        mock_enqueue,
        mock_sync,
    ):
        batch_id = uuid.uuid4()

        result = enqueue_content_sync(
            self.source,
            batch_id=batch_id,
            force=True,
        )

        self.assertFalse(result.ok)
        self.assertFalse(result.queued)
        self.assertTrue(result.ran_inline)
        self.assertEqual(result.source, self.source)
        self.assertEqual(result.batch_id, batch_id)
        self.assertEqual(result.error, 'inline sync error')
        self.assertEqual(
            result.message,
            'Sync failed for AI-Shipping-Labs/content: inline sync error',
        )
        mock_sync.assert_called_once_with(
            self.source,
            batch_id=batch_id,
            force=True,
        )
        self.source.refresh_from_db()
        self.assertIsNone(self.source.last_sync_status)
        self.assertFalse(SyncLog.objects.filter(source=self.source).exists())

    @patch('django_q.tasks.async_task', side_effect=Exception('queue error'))
    def test_async_enqueue_error_returns_failure_without_queued_state(
        self,
        mock_async,
    ):
        result = enqueue_content_sync(self.source)

        self.assertFalse(result.ok)
        self.assertFalse(result.queued)
        self.assertFalse(result.ran_inline)
        self.assertEqual(result.error, 'queue error')
        self.source.refresh_from_db()
        self.assertIsNone(self.source.last_sync_status)
        self.assertFalse(SyncLog.objects.filter(source=self.source).exists())


class ContentSyncQueueBulkServiceTest(TestCase):
    @patch('django_q.tasks.async_task')
    def test_bulk_enqueue_returns_one_result_per_source(self, mock_async):
        source_a = ContentSource.objects.create(repo_name='Org/a')
        source_b = ContentSource.objects.create(repo_name='Org/b')
        batch_id = uuid.uuid4()

        results = enqueue_content_syncs(
            [source_a, source_b],
            batch_id=batch_id,
        )

        self.assertEqual(len(results), 2)
        self.assertTrue(all(result.ok for result in results))
        self.assertEqual(mock_async.call_count, 2)
        self.assertEqual(
            SyncLog.objects.filter(batch_id=batch_id, status='queued').count(),
            2,
        )
