"""Tests for the studio worker status dashboard.

Verifies that:
- Worker liveness is driven by ``django_q.status.Stat.get_all()``
  (cluster heartbeat) rather than recent-task-completion proxy.
- The page shows three states: running+busy, running+idle, not running.
- Recent tasks are listed with status, duration, and error details.
- Queue depth (pending tasks) is displayed.
- Failed tasks section shows error details.
- Staff-only access is enforced.
"""

import re
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from django_q.models import OrmQ, Task

User = get_user_model()


def _create_task(**kwargs):
    """Create a django-q Task with an auto-generated id."""
    if 'id' not in kwargs:
        kwargs['id'] = uuid.uuid4().hex
    return Task.objects.create(**kwargs)


def _fake_cluster(
    cluster_id='cluster-abc',
    host='worker-1',
    pid=1234,
    workers=(101, 102),
    status='Idle',
    heartbeat_age_seconds=3.0,
    tob_seconds_ago=300.0,
    task_q_size=0,
    done_q_size=0,
):
    """Build a stand-in for ``django_q.status.Stat`` for tests.

    The real ``Stat.get_all()`` requires a running broker; we patch it to
    return these lightweight fakes that expose the same attributes the helper
    reads.
    """
    now = timezone.now()
    cluster = SimpleNamespace(
        cluster_id=cluster_id,
        host=host,
        pid=pid,
        workers=list(workers),
        status=status,
        timestamp=now - timedelta(seconds=heartbeat_age_seconds),
        tob=(now - timedelta(seconds=tob_seconds_ago)) if tob_seconds_ago else None,
        task_q_size=task_q_size,
        done_q_size=done_q_size,
    )
    cluster.uptime = lambda c=cluster: (timezone.now() - c.tob).total_seconds()
    return cluster


class WorkerStatusAccessTest(TestCase):
    """Test that the worker status page enforces staff-only access."""

    @classmethod
    def setUpTestData(cls):
        cls.staff_user = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        cls.regular_user = User.objects.create_user(
            email='user@test.com', password='testpass', is_staff=False,
        )

    def test_anonymous_redirects_to_login(self):
        response = self.client.get('/studio/worker/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)

    def test_non_staff_gets_403(self):
        self.client.login(email='user@test.com', password='testpass')
        response = self.client.get('/studio/worker/')
        self.assertEqual(response.status_code, 403)

    def test_staff_gets_200(self):
        self.client.login(email='staff@test.com', password='testpass')
        with patch('studio.worker_health.Stat.get_all', return_value=[]):
            response = self.client.get('/studio/worker/')
        self.assertEqual(response.status_code, 200)


class WorkerLivenessTest(TestCase):
    """Worker liveness is driven by cluster heartbeat, not recent tasks."""

    @classmethod
    def setUpTestData(cls):
        cls.staff_user = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

    def test_state_not_running_when_no_clusters(self):
        with patch('studio.worker_health.Stat.get_all', return_value=[]):
            response = self.client.get('/studio/worker/')
        self.assertFalse(response.context['worker_alive'])
        # Red banner with explicit "NOT running" wording and start command
        self.assertContains(response, 'Worker NOT running')
        self.assertContains(response, 'manage.py qcluster')

    def test_state_running_idle_when_cluster_present_no_queue(self):
        cluster = _fake_cluster(
            heartbeat_age_seconds=4.0, task_q_size=0, done_q_size=0,
        )
        with patch('studio.worker_health.Stat.get_all', return_value=[cluster]):
            response = self.client.get('/studio/worker/')
        self.assertTrue(response.context['worker_alive'])
        self.assertTrue(response.context['worker_idle'])
        self.assertContains(response, 'Worker running (idle)')

    def test_state_running_busy_when_queue_has_tasks(self):
        cluster = _fake_cluster(
            heartbeat_age_seconds=2.0, task_q_size=3, done_q_size=0,
        )
        with patch('studio.worker_health.Stat.get_all', return_value=[cluster]):
            response = self.client.get('/studio/worker/')
        self.assertTrue(response.context['worker_alive'])
        self.assertFalse(response.context['worker_idle'])
        # Should show "Worker running" but not "(idle)"
        self.assertContains(response, 'Worker running')
        self.assertNotContains(response, 'Worker running (idle)')

    def test_recent_completed_tasks_do_not_imply_alive(self):
        """The old heuristic checked Task.stopped recency; the new one ignores it.

        With recent successful tasks but no Stat heartbeat, the worker must be
        reported as NOT running — otherwise we falsely tell users everything is
        fine seconds after the worker died.
        """
        now = timezone.now()
        _create_task(
            name='recent-task',
            func='some.func',
            started=now - timedelta(seconds=30),
            stopped=now - timedelta(seconds=5),
            success=True,
        )
        with patch('studio.worker_health.Stat.get_all', return_value=[]):
            response = self.client.get('/studio/worker/')
        self.assertFalse(response.context['worker_alive'])
        self.assertContains(response, 'Worker NOT running')

    def test_idle_worker_with_no_recent_tasks_still_alive(self):
        """Old heuristic: idle for 5 min → 'Inactive'. New: stays alive."""
        cluster = _fake_cluster(heartbeat_age_seconds=5.0)
        with patch('studio.worker_health.Stat.get_all', return_value=[cluster]):
            response = self.client.get('/studio/worker/')
        self.assertTrue(response.context['worker_alive'])
        # No Task rows exist — but heartbeat is fresh
        self.assertEqual(Task.objects.count(), 0)

    def test_broker_error_reported_as_not_running(self):
        with patch(
            'studio.worker_health.Stat.get_all',
            side_effect=ConnectionError('broker unreachable'),
        ), self.assertLogs('studio.worker_health', level='WARNING') as logs:
            response = self.client.get('/studio/worker/')
        self.assertFalse(response.context['worker_alive'])
        self.assertContains(response, 'broker unreachable')
        self.assertIn(
            'Failed to query django-q cluster status: broker unreachable',
            logs.output[0],
        )

    def test_active_clusters_table_shows_cluster_metadata(self):
        cluster = _fake_cluster(
            cluster_id='abcdef123456',
            host='worker-host-1',
            workers=(11, 12, 13),
            heartbeat_age_seconds=1.0,
            tob_seconds_ago=600.0,
        )
        with patch('studio.worker_health.Stat.get_all', return_value=[cluster]):
            response = self.client.get('/studio/worker/')
        self.assertContains(response, 'worker-host-1')
        # 3 workers should be reported in cluster context
        clusters_ctx = response.context['worker_info']['clusters']
        self.assertEqual(clusters_ctx[0]['worker_count'], 3)


class WorkerStatusQueueDepthTest(TestCase):
    """Test that queue depth shows the number of pending tasks."""

    @classmethod
    def setUpTestData(cls):
        cls.staff_user = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')
        # All these tests don't care about cluster state; default to "no worker"
        # so each test has a deterministic baseline.
        patcher = patch('studio.worker_health.Stat.get_all', return_value=[])
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_queue_depth_zero_when_empty(self):
        response = self.client.get('/studio/worker/')
        self.assertEqual(response.context['queue_depth'], 0)

    def test_queue_depth_reflects_pending_tasks(self):
        OrmQ.objects.create(key='task-1', payload='{}')
        OrmQ.objects.create(key='task-2', payload='{}')
        response = self.client.get('/studio/worker/')
        self.assertEqual(response.context['queue_depth'], 2)


class WorkerStatusRecentTasksTest(TestCase):
    """Test that recent tasks are listed with correct details."""

    @classmethod
    def setUpTestData(cls):
        cls.staff_user = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')
        patcher = patch('studio.worker_health.Stat.get_all', return_value=[])
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_recent_tasks_displayed_in_table(self):
        now = timezone.now()
        _create_task(
            name='sync-content',
            func='integrations.services.github.sync_content_source',
            started=now - timedelta(seconds=30),
            stopped=now - timedelta(seconds=10),
            success=True,
        )
        response = self.client.get('/studio/worker/')
        self.assertContains(response, 'sync-content')
        self.assertContains(response, 'Success')

    def test_failed_task_shows_in_table(self):
        now = timezone.now()
        _create_task(
            name='failing-task',
            func='some.broken.func',
            started=now - timedelta(seconds=30),
            stopped=now - timedelta(seconds=20),
            success=False,
            result='ConnectionError: timeout',
        )
        response = self.client.get('/studio/worker/')
        self.assertContains(response, 'failing-task')
        self.assertContains(response, 'Failed')

    def test_empty_state_when_no_tasks(self):
        response = self.client.get('/studio/worker/')
        self.assertContains(response, 'No tasks recorded yet.')

    def test_success_and_failure_counts(self):
        now = timezone.now()
        _create_task(
            name='ok1', func='f', started=now, stopped=now, success=True,
        )
        _create_task(
            name='ok2', func='f', started=now, stopped=now, success=True,
        )
        _create_task(
            name='fail1', func='f', started=now, stopped=now, success=False,
        )
        response = self.client.get('/studio/worker/')
        self.assertEqual(response.context['success_count'], 2)
        self.assertEqual(response.context['failure_count'], 1)


class WorkerStatusFailedTasksTest(TestCase):
    """Test that the failed tasks section shows error details."""

    @classmethod
    def setUpTestData(cls):
        cls.staff_user = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')
        patcher = patch('studio.worker_health.Stat.get_all', return_value=[])
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_failed_tasks_section_shows_error_details(self):
        now = timezone.now()
        _create_task(
            name='broken-sync',
            func='integrations.services.github.sync_content_source',
            started=now - timedelta(seconds=60),
            stopped=now - timedelta(seconds=50),
            success=False,
            result='RuntimeError: GitHub API rate limit exceeded',
        )
        response = self.client.get('/studio/worker/')
        self.assertEqual(len(response.context['failed_with_details']), 1)
        self.assertContains(response, 'Failed Tasks')
        self.assertContains(response, 'broken-sync')
        self.assertContains(response, 'RuntimeError: GitHub API rate limit exceeded')

    def test_no_failed_section_when_all_succeed(self):
        now = timezone.now()
        _create_task(
            name='good-task', func='f', started=now, stopped=now, success=True,
        )
        response = self.client.get('/studio/worker/')
        self.assertEqual(len(response.context['failed_with_details']), 0)


class WorkerStatusFailedTaskCollapseTest(TestCase):
    """Failed-task tracebacks render collapsed by default with a one-line summary.

    See issue #218: long stack traces used to render inline and turned the panel
    into a wall of red text. Each row now shows a summary line plus a hidden
    ``<pre>`` block that the inline toggle script reveals.
    """

    @classmethod
    def setUpTestData(cls):
        cls.staff_user = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')
        patcher = patch('studio.worker_health.Stat.get_all', return_value=[])
        patcher.start()
        self.addCleanup(patcher.stop)

    def _multiline_traceback(self):
        return (
            'Traceback (most recent call last):\n'
            '  File "/app/integrations/services/github.py", line 42, in sync_content_source\n'
            '    response = client.get(url)\n'
            '  File "/app/.venv/lib/requests/api.py", line 73, in get\n'
            '    return request("get", url, params=params, **kwargs)\n'
            'RuntimeError: GitHub API rate limit exceeded'
        )

    def test_error_summary_for_traceback_uses_last_line(self):
        """For ``traceback.format_exc()`` output the first line is the literal
        ``Traceback (most recent call last):`` banner — useless as a summary
        because every failure shares it. The exception class + message lives
        on the last non-blank line, which is what operators need to see in the
        collapsed view."""
        now = timezone.now()
        _create_task(
            name='broken-sync',
            func='integrations.services.github.sync_content_source',
            started=now,
            stopped=now,
            success=False,
            result=self._multiline_traceback(),
        )
        response = self.client.get('/studio/worker/')
        items = response.context['failed_with_details']
        self.assertEqual(len(items), 1)
        self.assertEqual(
            items[0]['error_summary'],
            'RuntimeError: GitHub API rate limit exceeded',
        )

    def test_error_summary_for_non_traceback_uses_first_line(self):
        """Non-traceback results (custom error wrappers, plain strings) keep
        the first-non-blank-line summary because there is no banner to skip."""
        now = timezone.now()
        _create_task(
            name='custom-error',
            func='f',
            started=now,
            stopped=now,
            success=False,
            result='RuntimeError: boom\nadditional context line\nanother line',
        )
        response = self.client.get('/studio/worker/')
        items = response.context['failed_with_details']
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['error_summary'], 'RuntimeError: boom')

    def test_error_summary_truncated_when_first_line_is_long(self):
        now = timezone.now()
        long_first_line = 'X' * 500
        _create_task(
            name='long-error',
            func='f',
            started=now,
            stopped=now,
            success=False,
            result=long_first_line + '\nsecond line',
        )
        response = self.client.get('/studio/worker/')
        summary = response.context['failed_with_details'][0]['error_summary']
        self.assertLessEqual(len(summary), 160)
        self.assertTrue(summary.endswith('...'))

    def test_error_summary_falls_back_when_result_is_blank(self):
        now = timezone.now()
        _create_task(
            name='blank-error',
            func='f',
            started=now,
            stopped=now,
            success=False,
            result='   \n  \n',
        )
        response = self.client.get('/studio/worker/')
        self.assertEqual(
            response.context['failed_with_details'][0]['error_summary'],
            'No error details',
        )

    def test_traceback_pre_block_is_hidden_by_default(self):
        """The <pre> rendering the full traceback must carry the ``hidden`` class.

        We assert on the specific element wrapping the traceback rather than the
        page body, so the test only passes when the collapse markup is correct.
        """
        now = timezone.now()
        _create_task(
            name='broken-sync',
            func='f',
            started=now,
            stopped=now,
            success=False,
            result=self._multiline_traceback(),
        )
        response = self.client.get('/studio/worker/')
        html = response.content.decode()
        # Find the trace pre block that contains the traceback text.
        match = re.search(
            r'<pre[^>]*class="([^"]*failed-task-trace[^"]*)"[^>]*>'
            r'[^<]*Traceback',
            html,
        )
        self.assertIsNotNone(
            match,
            'Expected a <pre class="...failed-task-trace..."> wrapping the traceback',
        )
        self.assertIn(
            'hidden', match.group(1),
            'Traceback <pre> must start with the "hidden" class so the panel '
            'is collapsed by default.',
        )

    def test_toggle_button_present_with_aria_collapsed(self):
        now = timezone.now()
        _create_task(
            name='broken-sync',
            func='f',
            started=now,
            stopped=now,
            success=False,
            result='RuntimeError: boom',
        )
        response = self.client.get('/studio/worker/')
        # The clickable summary row exposes the action hook used by the JS
        # toggle script and is collapsed at first paint.
        self.assertContains(
            response,
            'data-action="toggle-failed-trace"',
        )
        self.assertContains(response, 'aria-expanded="false"')

    def test_expand_collapse_all_controls_present(self):
        now = timezone.now()
        _create_task(
            name='broken-sync',
            func='f',
            started=now,
            stopped=now,
            success=False,
            result='RuntimeError: boom',
        )
        response = self.client.get('/studio/worker/')
        self.assertContains(response, 'data-action="expand-all-failed"')
        self.assertContains(response, 'data-action="collapse-all-failed"')

    def test_full_error_message_still_in_response(self):
        """Collapse only hides via CSS — the full text must still render so the
        user can copy it after expanding (and so existing assertions on the
        traceback body keep working)."""
        now = timezone.now()
        _create_task(
            name='broken-sync',
            func='f',
            started=now,
            stopped=now,
            success=False,
            result=self._multiline_traceback(),
        )
        response = self.client.get('/studio/worker/')
        self.assertContains(response, 'GitHub API rate limit exceeded')
        self.assertContains(response, 'Traceback (most recent call last):')


class WorkerStatusTemplateTest(TestCase):
    """Test that the worker page uses the correct template and has sidebar link."""

    @classmethod
    def setUpTestData(cls):
        cls.staff_user = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')
        patcher = patch('studio.worker_health.Stat.get_all', return_value=[])
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_uses_correct_template(self):
        response = self.client.get('/studio/worker/')
        self.assertTemplateUsed(response, 'studio/worker.html')
        self.assertTemplateUsed(response, 'studio/base.html')

    def test_sidebar_has_worker_link(self):
        response = self.client.get('/studio/')
        self.assertContains(response, 'Worker')
        self.assertContains(response, '/studio/worker/')
