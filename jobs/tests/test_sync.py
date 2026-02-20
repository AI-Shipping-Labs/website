"""
Tests for end-to-end task execution in sync mode.

Django-Q2's sync mode executes tasks inline (no worker needed),
which is ideal for testing that the full pipeline works.
"""

from django.test import TestCase, override_settings

from django_q.tasks import async_task as q_async_task
from django_q.models import Task


# Override Q_CLUSTER to use sync mode for these tests
Q_CLUSTER_SYNC = {
    'name': 'test',
    'sync': True,
    'orm': 'default',
}


@override_settings(Q_CLUSTER=Q_CLUSTER_SYNC)
class SyncTaskExecutionTest(TestCase):
    """Tests that tasks execute end-to-end in sync mode."""

    def test_health_check_runs_end_to_end(self):
        """Health check task executes and returns result in sync mode."""
        task_id = q_async_task(
            'jobs.tasks.healthcheck.health_check',
            sync=True,
        )
        # Fetch the task from the database
        task = Task.objects.get(id=task_id)
        self.assertTrue(task.success)
        self.assertIsNotNone(task.result)
        self.assertEqual(task.result['status'], 'ok')
        self.assertIn('timestamp', task.result)

    def test_cleanup_task_runs_end_to_end(self):
        """Cleanup task executes without error in sync mode."""
        task_id = q_async_task(
            'jobs.tasks.cleanup.cleanup_old_webhook_logs',
            30,
            sync=True,
        )
        # Fetch the task from the database
        task = Task.objects.get(id=task_id)
        self.assertTrue(task.success)
        self.assertIsNotNone(task.result)
        self.assertEqual(task.result['deleted'], 0)
        self.assertEqual(task.result['cutoff_days'], 30)

    def test_failed_task_raises_in_sync_mode(self):
        """In sync mode, a failing task propagates the exception.

        In production (async mode), the worker catches exceptions and stores
        them as failed tasks with tracebacks in the Failure model. In sync
        mode, exceptions propagate directly, which is useful for debugging.
        """
        with self.assertRaises(ValueError) as ctx:
            q_async_task(
                'jobs.tests.test_sync.failing_task',
                sync=True,
            )
        self.assertIn("Intentional test failure", str(ctx.exception))


def failing_task():
    """A task that always raises an error, used for testing failure logging."""
    raise ValueError("Intentional test failure")
