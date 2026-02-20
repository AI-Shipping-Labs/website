"""
Tests for the jobs.tasks helper functions (async_task, schedule).
"""

from unittest.mock import patch, MagicMock

from django.test import TestCase, override_settings

from django_q.models import Schedule

from jobs.tasks.helpers import async_task, schedule


class AsyncTaskTest(TestCase):
    """Tests for the async_task helper."""

    @patch('jobs.tasks.helpers.q_async_task')
    def test_async_task_calls_django_q(self, mock_q_async):
        """async_task delegates to django_q.tasks.async_task."""
        mock_q_async.return_value = 'task-123'
        result = async_task('myapp.tasks.do_thing', 1, 2, key='value')
        self.assertEqual(result, 'task-123')
        mock_q_async.assert_called_once()
        call_args = mock_q_async.call_args
        self.assertEqual(call_args[0][0], 'myapp.tasks.do_thing')
        self.assertEqual(call_args[0][1], 1)
        self.assertEqual(call_args[0][2], 2)
        self.assertEqual(call_args[1]['key'], 'value')

    @patch('jobs.tasks.helpers.q_async_task')
    def test_async_task_with_max_retries(self, mock_q_async):
        """async_task passes max_attempts via q_options."""
        mock_q_async.return_value = 'task-456'
        async_task('myapp.tasks.do_thing', max_retries=5)
        call_args = mock_q_async.call_args
        q_options = call_args[1]['q_options']
        # max_retries=5 means 6 total attempts
        self.assertEqual(q_options['max_attempts'], 6)

    @patch('jobs.tasks.helpers.q_async_task')
    def test_async_task_with_retry_backoff(self, mock_q_async):
        """async_task passes retry backoff via q_options."""
        mock_q_async.return_value = 'task-789'
        async_task('myapp.tasks.do_thing', retry_backoff=120)
        call_args = mock_q_async.call_args
        q_options = call_args[1]['q_options']
        self.assertEqual(q_options['retry'], 120)

    @patch('jobs.tasks.helpers.q_async_task')
    def test_async_task_uses_default_max_attempts_from_settings(self, mock_q_async):
        """async_task uses Q_CLUSTER max_attempts when not explicitly set."""
        mock_q_async.return_value = 'task-abc'
        async_task('myapp.tasks.do_thing')
        call_args = mock_q_async.call_args
        q_options = call_args[1]['q_options']
        # Default from settings is 3
        self.assertEqual(q_options['max_attempts'], 3)

    @patch('jobs.tasks.helpers.q_async_task')
    def test_async_task_returns_task_id(self, mock_q_async):
        """async_task returns the task ID from Django-Q."""
        mock_q_async.return_value = 'unique-task-id'
        result = async_task('myapp.tasks.do_thing')
        self.assertEqual(result, 'unique-task-id')


class ScheduleTest(TestCase):
    """Tests for the schedule helper."""

    def test_schedule_creates_schedule_object(self):
        """schedule() creates a Schedule model instance."""
        obj = schedule('jobs.tasks.healthcheck.health_check', cron='*/15 * * * *')
        self.assertIsInstance(obj, Schedule)
        self.assertEqual(obj.func, 'jobs.tasks.healthcheck.health_check')
        self.assertEqual(obj.cron, '*/15 * * * *')
        self.assertEqual(obj.schedule_type, Schedule.CRON)
        self.assertEqual(obj.repeats, -1)

    def test_schedule_with_custom_name(self):
        """schedule() uses provided name."""
        obj = schedule(
            'jobs.tasks.healthcheck.health_check',
            cron='0 * * * *',
            name='my-health-check',
        )
        self.assertEqual(obj.name, 'my-health-check')

    def test_schedule_defaults_name_to_func(self):
        """schedule() defaults name to the function path."""
        obj = schedule('jobs.tasks.healthcheck.health_check', cron='0 * * * *')
        self.assertEqual(obj.name, 'jobs.tasks.healthcheck.health_check')

    def test_schedule_update_or_create(self):
        """schedule() updates existing schedule instead of creating duplicates."""
        obj1 = schedule(
            'jobs.tasks.healthcheck.health_check',
            cron='0 * * * *',
            name='test-schedule',
        )
        obj2 = schedule(
            'jobs.tasks.healthcheck.health_check',
            cron='*/30 * * * *',
            name='test-schedule',
        )
        # Should be the same object, updated
        self.assertEqual(obj1.pk, obj2.pk)
        self.assertEqual(obj2.cron, '*/30 * * * *')
        # Only one schedule should exist with this name
        self.assertEqual(Schedule.objects.filter(name='test-schedule').count(), 1)

    def test_schedule_requires_cron(self):
        """schedule() raises ValueError if cron is not provided."""
        with self.assertRaises(ValueError):
            schedule('jobs.tasks.healthcheck.health_check')

    def test_schedule_with_kwargs(self):
        """schedule() stores kwargs for the scheduled function."""
        obj = schedule(
            'jobs.tasks.cleanup.cleanup_old_webhook_logs',
            cron='0 3 * * *',
            name='cleanup-test',
            days=60,
        )
        self.assertEqual(obj.kwargs, {'days': 60})

    def test_schedule_with_repeats(self):
        """schedule() accepts a custom repeats value."""
        obj = schedule(
            'jobs.tasks.healthcheck.health_check',
            cron='0 * * * *',
            name='limited-schedule',
            repeats=5,
        )
        self.assertEqual(obj.repeats, 5)
