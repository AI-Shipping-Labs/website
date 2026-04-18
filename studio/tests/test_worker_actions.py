"""Tests for the studio worker dashboard operational actions.

Covers (per issue #215):
- Drain queue (deletes every pending OrmQ row, with count in flash message).
- Inspect a single queued task.
- Delete a single queued task.
- Retry / delete a single failed Task.
- Bulk retry / bulk delete failed Tasks.
- Conditional rendering of action buttons.

Content-sync triggers live on /studio/sync/ — they are intentionally not
exposed on the worker page (see issue #240).
"""

import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import TestCase
from django.utils import timezone
from django_q.models import OrmQ, Task
from django_q.signing import SignedPackage

User = get_user_model()


def _fake_alive_cluster():
    """Return a SimpleNamespace mimicking django_q.status.Stat for an alive worker."""
    now = timezone.now()
    cluster = SimpleNamespace(
        cluster_id='cluster-abc',
        host='worker-1',
        pid=1234,
        workers=[101, 102],
        status='Idle',
        timestamp=now - timedelta(seconds=3),
        tob=now - timedelta(seconds=300),
        task_q_size=0,
        done_q_size=0,
    )
    cluster.uptime = lambda c=cluster: (timezone.now() - c.tob).total_seconds()
    return cluster


def _create_task(success=False, result=None, **kwargs):
    """Create a django-q Task row with sensible defaults."""
    now = timezone.now()
    defaults = {
        'id': kwargs.pop('id', uuid.uuid4().hex),
        'name': kwargs.pop('name', 'sync-content-AI-Shipping-Labs/blog'),
        'func': kwargs.pop(
            'func', 'integrations.services.github.sync_content_source',
        ),
        'args': kwargs.pop('args', ()),
        'kwargs': kwargs.pop('kwargs', {}),
        'started': kwargs.pop('started', now - timedelta(seconds=30)),
        'stopped': kwargs.pop('stopped', now - timedelta(seconds=10)),
        'success': success,
        'result': result,
    }
    defaults.update(kwargs)
    return Task.objects.create(**defaults)


def _enqueue_ormq(name='sync-content-blog', func='integrations.services.github.sync_content_source',
                  args=(1, 'two'), kwargs=None, lock_age_seconds=12):
    """Write a real signed OrmQ row so the inspect view can decode it."""
    payload = {
        'id': uuid.uuid4().hex,
        'name': name,
        'func': func,
        'args': args,
        'kwargs': kwargs or {},
    }
    signed = SignedPackage.dumps(payload)
    return OrmQ.objects.create(
        key='default',
        payload=signed,
        lock=timezone.now() - timedelta(seconds=lock_age_seconds),
    )


class WorkerActionsAccessTest(TestCase):
    """All action endpoints require staff and POST (where applicable)."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        cls.regular = User.objects.create_user(
            email='user@test.com', password='testpass', is_staff=False,
        )

    def test_drain_requires_post(self):
        self.client.login(email='staff@test.com', password='testpass')
        response = self.client.get('/studio/worker/queue/drain/')
        self.assertEqual(response.status_code, 405)

    def test_drain_requires_staff(self):
        self.client.login(email='user@test.com', password='testpass')
        response = self.client.post('/studio/worker/queue/drain/')
        self.assertEqual(response.status_code, 403)

    def test_drain_anonymous_redirects_to_login(self):
        response = self.client.post('/studio/worker/queue/drain/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)

    def test_run_sync_now_endpoint_is_gone(self):
        """Removed in #240 — sync triggers belong on /studio/sync/."""
        self.client.login(email='staff@test.com', password='testpass')
        response = self.client.post('/studio/worker/run-sync-now/')
        self.assertEqual(response.status_code, 404)

    def test_inspect_requires_staff(self):
        ormq = _enqueue_ormq()
        self.client.login(email='user@test.com', password='testpass')
        response = self.client.get(
            f'/studio/worker/queue/{ormq.pk}/inspect/',
        )
        self.assertEqual(response.status_code, 403)

    def test_bulk_retry_requires_post(self):
        self.client.login(email='staff@test.com', password='testpass')
        response = self.client.get('/studio/worker/failed/bulk-retry/')
        self.assertEqual(response.status_code, 405)

    def test_bulk_delete_requires_post(self):
        self.client.login(email='staff@test.com', password='testpass')
        response = self.client.get('/studio/worker/failed/bulk-delete/')
        self.assertEqual(response.status_code, 405)


class DrainQueueTest(TestCase):
    """Drain wipes every OrmQ row and reports how many were deleted."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

    def test_drain_deletes_all_pending_ormq(self):
        for i in range(5):
            _enqueue_ormq(name=f'task-{i}')
        self.assertEqual(OrmQ.objects.count(), 5)

        response = self.client.post('/studio/worker/queue/drain/')

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/studio/worker/')
        self.assertEqual(OrmQ.objects.count(), 0)

    def test_drain_message_contains_count(self):
        for i in range(3):
            _enqueue_ormq(name=f'task-{i}')

        response = self.client.post(
            '/studio/worker/queue/drain/', follow=True,
        )

        msgs = [str(m) for m in get_messages(response.wsgi_request)]
        self.assertTrue(
            any('3' in m and 'pending' in m.lower() for m in msgs),
            f'Expected drain message with count 3; got {msgs!r}',
        )

    def test_drain_when_empty_uses_info_message(self):
        response = self.client.post(
            '/studio/worker/queue/drain/', follow=True,
        )
        msgs = [str(m) for m in get_messages(response.wsgi_request)]
        self.assertTrue(
            any('empty' in m.lower() for m in msgs),
            f'Expected "queue empty" info message; got {msgs!r}',
        )

    def test_drain_button_only_renders_when_depth_positive(self):
        """Buttons are gated by queue depth so the dashboard stays clean."""
        with patch('studio.worker_health.Stat.get_all', return_value=[]):
            empty_response = self.client.get('/studio/worker/')
        self.assertNotContains(empty_response, 'data-action="drain-queue"')

        _enqueue_ormq(name='task-1')
        with patch('studio.worker_health.Stat.get_all', return_value=[]):
            full_response = self.client.get('/studio/worker/')
        self.assertContains(full_response, 'data-action="drain-queue"')
        # And the count is rendered in the button label
        self.assertContains(full_response, 'Drain queue (1)')


class InspectQueuedTaskTest(TestCase):
    """Inspect shows func/args/kwargs/age for a single queued task."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

    def test_inspect_shows_func_args_kwargs(self):
        ormq = _enqueue_ormq(
            name='inspect-me',
            func='integrations.services.github.sync_content_source',
            args=('source-uuid-1', 'extra'),
            kwargs={'batch_id': 'b-1'},
            lock_age_seconds=42,
        )
        response = self.client.get(
            f'/studio/worker/queue/{ormq.pk}/inspect/',
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'inspect-me')
        self.assertContains(response, 'sync_content_source')
        self.assertContains(response, 'source-uuid-1')
        self.assertContains(response, 'batch_id')
        # Age in seconds is shown (rounded). 42s lock → "42s" in template.
        self.assertContains(response, '42s')

    def test_inspect_404_when_missing(self):
        response = self.client.get('/studio/worker/queue/9999/inspect/')
        self.assertEqual(response.status_code, 404)

    def test_inspect_link_in_pending_table(self):
        ormq = _enqueue_ormq(name='visible-in-list')
        with patch('studio.worker_health.Stat.get_all', return_value=[]):
            response = self.client.get('/studio/worker/')
        self.assertContains(response, 'visible-in-list')
        self.assertContains(
            response, f'/studio/worker/queue/{ormq.pk}/inspect/',
        )


class DeleteQueuedTaskTest(TestCase):
    """Delete removes a single OrmQ row, leaving siblings intact."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

    def test_delete_removes_single_row(self):
        keep = _enqueue_ormq(name='keep-me')
        target = _enqueue_ormq(name='delete-me')

        response = self.client.post(
            f'/studio/worker/queue/{target.pk}/delete/',
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(OrmQ.objects.filter(pk=target.pk).exists())
        self.assertTrue(OrmQ.objects.filter(pk=keep.pk).exists())

    def test_delete_404_when_missing(self):
        response = self.client.post('/studio/worker/queue/9999/delete/')
        self.assertEqual(response.status_code, 404)


class RetryFailedTaskTest(TestCase):
    """Retry re-enqueues a failed task with the same func/args/kwargs."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

    @patch('studio.views.worker.async_task')
    def test_retry_re_enqueues_with_original_args(self, mock_async):
        task = _create_task(
            success=False,
            args=('arg1', 'arg2'),
            kwargs={'k': 'v'},
            result='boom',
        )
        response = self.client.post(
            f'/studio/worker/failed/{task.pk}/retry/',
        )
        self.assertEqual(response.status_code, 302)
        mock_async.assert_called_once()
        call_args = mock_async.call_args
        self.assertEqual(call_args[0][0], task.func)
        self.assertEqual(call_args[0][1:], ('arg1', 'arg2'))
        self.assertEqual(call_args[1].get('k'), 'v')

    @patch('studio.views.worker.async_task')
    def test_retry_deletes_the_failed_row(self, mock_async):
        task = _create_task(success=False, result='boom')
        self.client.post(f'/studio/worker/failed/{task.pk}/retry/')
        self.assertFalse(Task.objects.filter(pk=task.pk).exists())

    @patch('studio.views.worker.async_task')
    def test_retry_404_for_unknown_id(self, mock_async):
        response = self.client.post('/studio/worker/failed/abc123/retry/')
        self.assertEqual(response.status_code, 404)
        mock_async.assert_not_called()

    @patch('studio.views.worker.async_task')
    def test_retry_404_when_task_is_successful(self, mock_async):
        """We never re-enqueue success rows from this endpoint."""
        ok = _create_task(success=True)
        response = self.client.post(f'/studio/worker/failed/{ok.pk}/retry/')
        self.assertEqual(response.status_code, 404)
        mock_async.assert_not_called()


class DeleteFailedTaskTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

    def test_delete_removes_failed_row(self):
        task = _create_task(success=False, result='boom')
        response = self.client.post(
            f'/studio/worker/failed/{task.pk}/delete/',
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Task.objects.filter(pk=task.pk).exists())

    def test_delete_404_when_missing(self):
        response = self.client.post('/studio/worker/failed/missing/delete/')
        self.assertEqual(response.status_code, 404)


class BulkFailedActionsTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

    @patch('studio.views.worker.async_task')
    def test_bulk_retry_requeues_every_failed_and_keeps_successes(
        self, mock_async,
    ):
        for i in range(3):
            _create_task(success=False, result=f'boom-{i}')
        ok = _create_task(success=True)

        response = self.client.post('/studio/worker/failed/bulk-retry/')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(mock_async.call_count, 3)
        self.assertEqual(Task.objects.filter(success=False).count(), 0)
        self.assertTrue(Task.objects.filter(pk=ok.pk).exists())

    @patch('studio.views.worker.async_task')
    def test_bulk_retry_when_no_failures(self, mock_async):
        response = self.client.post(
            '/studio/worker/failed/bulk-retry/', follow=True,
        )
        mock_async.assert_not_called()
        msgs = [str(m) for m in get_messages(response.wsgi_request)]
        self.assertTrue(
            any('no failed' in m.lower() for m in msgs),
            f'Expected "no failed tasks" message; got {msgs!r}',
        )

    def test_bulk_delete_removes_all_failures_keeps_successes(self):
        for _ in range(4):
            _create_task(success=False, result='boom')
        ok = _create_task(success=True)
        self.assertEqual(Task.objects.filter(success=False).count(), 4)

        response = self.client.post('/studio/worker/failed/bulk-delete/')

        self.assertEqual(response.status_code, 302)
        self.assertEqual(Task.objects.filter(success=False).count(), 0)
        self.assertTrue(Task.objects.filter(pk=ok.pk).exists())

    def test_bulk_buttons_only_when_failures_exist(self):
        with patch('studio.worker_health.Stat.get_all', return_value=[]):
            empty_response = self.client.get('/studio/worker/')
        self.assertNotContains(empty_response, 'data-action="bulk-retry"')
        self.assertNotContains(empty_response, 'data-action="bulk-delete"')

        _create_task(success=False, result='boom')
        with patch('studio.worker_health.Stat.get_all', return_value=[]):
            response = self.client.get('/studio/worker/')
        self.assertContains(response, 'data-action="bulk-retry"')
        self.assertContains(response, 'data-action="bulk-delete"')


class RunSyncButtonRemovedTest(TestCase):
    """Per #240, the 'Run sync now' button must not appear on /studio/worker/.

    Sync triggers live on /studio/sync/. The button conflated worker
    monitoring with content-sync operator actions.
    """

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

    def test_run_sync_button_absent_when_worker_dead(self):
        with patch('studio.worker_health.Stat.get_all', return_value=[]):
            response = self.client.get('/studio/worker/')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-action="run-sync-now"')
        self.assertNotContains(response, 'Run sync now')

    def test_run_sync_button_absent_when_worker_alive(self):
        with patch(
            'studio.worker_health.Stat.get_all',
            return_value=[_fake_alive_cluster()],
        ):
            response = self.client.get('/studio/worker/')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-action="run-sync-now"')
        self.assertNotContains(response, 'Run sync now')

    def test_drain_queue_button_still_present_when_queue_nonempty(self):
        """Regression guard: removing the sync button must not nuke drain."""
        _enqueue_ormq(name='still-here')
        with patch('studio.worker_health.Stat.get_all', return_value=[]):
            response = self.client.get('/studio/worker/')
        self.assertContains(response, 'data-action="drain-queue"')


class WorkerDashboardRendersQueuedTasksTest(TestCase):
    """The pending-tasks table shows func + age and exposes Inspect/Delete buttons."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

    def test_pending_tasks_section_lists_queued_tasks(self):
        _enqueue_ormq(name='visible-task', lock_age_seconds=7)
        with patch('studio.worker_health.Stat.get_all', return_value=[]):
            response = self.client.get('/studio/worker/')
        self.assertContains(response, 'Pending Tasks (1)')
        self.assertContains(response, 'visible-task')
        self.assertContains(response, '7s')
        self.assertContains(response, 'data-action="delete-queued"')

    def test_pending_section_hidden_when_queue_empty(self):
        with patch('studio.worker_health.Stat.get_all', return_value=[]):
            response = self.client.get('/studio/worker/')
        self.assertNotContains(response, 'Pending Tasks (')
