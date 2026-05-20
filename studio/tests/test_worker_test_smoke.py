"""Tests for the 'Test worker' smoke-test action on /studio/worker/.

Issue #697: a staff-only button that enqueues a tiny ``async_task`` so the
operator can confirm the worker is alive after a deploy/restart.

Covers:
- Button renders on the worker page for staff.
- Staff POST enqueues the smoke task and flashes the task_id.
- Anonymous POST redirects to login.
- The underlying task function logs and returns the hello-world string.
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import TestCase

from jobs.tasks.test_worker_smoke import run as smoke_run

User = get_user_model()


class WorkerSmokeTaskFunctionTest(TestCase):
    """The task function logs the hello-world line and returns it."""

    def test_run_returns_hello_world_with_host_and_pid(self):
        result = smoke_run()
        self.assertIn('hello world from worker on', result)
        self.assertIn('pid=', result)

    def test_run_emits_log_line(self):
        with self.assertLogs('jobs.tasks.test_worker_smoke', level='INFO') as logs:
            smoke_run()
        # The log message uses %-formatting with hostname + PID — the
        # rendered output must include the literal "hello world" phrase plus
        # the pid= marker so operators can spot it in worker logs.
        joined = '\n'.join(logs.output)
        self.assertIn('hello world from worker on', joined)
        self.assertIn('pid=', joined)


class WorkerTestSmokeButtonRenderTest(TestCase):
    """The 'Test worker' button renders on the worker dashboard for staff."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')
        patcher = patch('studio.worker_health.Stat.get_all', return_value=[])
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_button_present_for_staff(self):
        response = self.client.get('/studio/worker/')
        self.assertEqual(response.status_code, 200)
        # The button carries a stable data-testid hook plus the literal label.
        self.assertContains(response, 'data-testid="test-worker-button"')
        self.assertContains(response, 'Test worker')

    def test_button_posts_to_smoke_url(self):
        response = self.client.get('/studio/worker/')
        self.assertContains(response, 'action="/studio/worker/test/"')


class WorkerTestSmokePostTest(TestCase):
    """POST /studio/worker/test/ enqueues a task and flashes the id."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        cls.regular = User.objects.create_user(
            email='user@test.com', password='testpass', is_staff=False,
        )

    @patch('studio.views.worker.async_task')
    def test_staff_post_enqueues_smoke_task(self, mock_async):
        mock_async.return_value = 'task-abc123'
        self.client.login(email='staff@test.com', password='testpass')
        response = self.client.post('/studio/worker/test/')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/studio/worker/')
        mock_async.assert_called_once()
        args, kwargs = mock_async.call_args
        self.assertEqual(args, ('jobs.tasks.test_worker_smoke.run',))
        # Issue #717: every async_task() must pass a descriptive task_name so
        # the resulting worker-history row isn't a random Django-Q codename.
        self.assertEqual(
            kwargs['task_name'],
            'Test worker smoke: queue dispatch from Studio worker page',
        )

    @patch('studio.views.worker.async_task')
    def test_flash_contains_task_id(self, mock_async):
        mock_async.return_value = 'task-abc123'
        self.client.login(email='staff@test.com', password='testpass')
        response = self.client.post('/studio/worker/test/', follow=False)
        flashes = [str(m) for m in get_messages(response.wsgi_request)]
        self.assertEqual(len(flashes), 1)
        self.assertIn('Test task queued', flashes[0])
        self.assertIn('task-abc123', flashes[0])

    @patch('studio.views.worker.async_task')
    def test_get_method_not_allowed(self, mock_async):
        """The endpoint is POST-only — GET must not enqueue a task."""
        self.client.login(email='staff@test.com', password='testpass')
        response = self.client.get('/studio/worker/test/')
        self.assertEqual(response.status_code, 405)
        mock_async.assert_not_called()

    @patch('studio.views.worker.async_task')
    def test_anonymous_post_redirects_to_login(self, mock_async):
        response = self.client.post('/studio/worker/test/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)
        mock_async.assert_not_called()

    @patch('studio.views.worker.async_task')
    def test_non_staff_post_forbidden(self, mock_async):
        self.client.login(email='user@test.com', password='testpass')
        response = self.client.post('/studio/worker/test/')
        self.assertEqual(response.status_code, 403)
        mock_async.assert_not_called()

