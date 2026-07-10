"""Tests for issue #242: stale-OrmQ recovery and the task-detail view.

Covers:

- Clicking ``Inspect`` on a queued row that the worker just consumed redirects
  to the matching completed-Task detail view (option C from the issue's UX
  discussion) when ``?task_id=`` is supplied.
- Same row with no matching Task → soft-fail to ``/studio/worker/`` with the
  ``"Task already finished or removed from the queue."`` info flash.
- Same recovery for the POST delete endpoint (carries ``task_id`` in the form
  body) — never 404 on a delete-of-already-deleted.
- The new ``/studio/worker/task/<task_id>/`` detail view renders duration,
  args, kwargs, and either a pretty-printed return value (success) or a
  collapsible traceback (failure).
- The Recent Tasks table on the dashboard links to the new detail view.
- ``?fragment=pending`` returns the pending-tasks partial only (no chrome,
  full-page sidebar absent).
"""

import uuid
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import TestCase
from django.utils import timezone
from django_q.models import OrmQ, Task
from django_q.signing import SignedPackage

User = get_user_model()


def _create_task(success=True, result=None, name='completed-task', **kwargs):
    now = timezone.now()
    defaults = {
        'id': kwargs.pop('id', uuid.uuid4().hex),
        'name': name,
        'func': kwargs.pop('func', 'integrations.services.github.sync_content_source'),
        'args': kwargs.pop('args', ()),
        'kwargs': kwargs.pop('kwargs', {}),
        'started': kwargs.pop('started', now - timedelta(seconds=12)),
        'stopped': kwargs.pop('stopped', now - timedelta(seconds=2)),
        'success': success,
        'result': result,
    }
    defaults.update(kwargs)
    return Task.objects.create(**defaults)


def _enqueue_ormq(task_id=None, name='queued-task',
                  func='integrations.services.github.sync_content_source',
                  args=(), kwargs=None, lock_age_seconds=12):
    payload = {
        'id': task_id or uuid.uuid4().hex,
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
    ), payload['id']


def _fake_dead_clusters():
    """Patch target: keep the worker-status banner deterministic."""
    return patch('studio.worker_health.Stat.get_all', return_value=[])


class StaleOrmQInspectTest(TestCase):
    """Inspect a pending row that the worker already consumed."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

    def test_inspect_stale_with_completed_task_redirects_to_detail(self):
        """Worker consumed the row and finished it → land on task detail."""
        # Pretend the OrmQ row used to exist (pk we'll click) and a Task was
        # written. We only need the Task — the OrmQ deletion is implicit.
        task = _create_task(name='already-ran', success=True, result='ok')
        # The id 9999 is the stale OrmQ pk the operator clicks.
        response = self.client.get(
            f'/studio/worker/queue/9999/inspect/?task_id={task.id}',
        )
        self.assertRedirects(
            response, f'/studio/worker/task/{task.id}/',
            fetch_redirect_response=False,
        )

    def test_inspect_stale_without_task_redirects_to_dashboard_with_flash(self):
        """No Task row either (drained, never ran) → flash + back to dash."""
        response = self.client.get(
            '/studio/worker/queue/9999/inspect/?task_id=00000000000000000000000000000000',
            follow=True,
        )
        self.assertRedirects(response, '/studio/worker/')
        msgs = [str(m) for m in get_messages(response.wsgi_request)]
        self.assertTrue(
            any('already finished' in m.lower() for m in msgs),
            f'Expected stale-OrmQ info flash; got {msgs!r}',
        )

    def test_inspect_stale_without_task_id_query_param_falls_back_to_flash(self):
        """Pre-#242 bookmarks (no ?task_id=) still get a friendly flash."""
        response = self.client.get(
            '/studio/worker/queue/9999/inspect/', follow=True,
        )
        self.assertRedirects(response, '/studio/worker/')
        msgs = [str(m) for m in get_messages(response.wsgi_request)]
        self.assertTrue(any('already finished' in m.lower() for m in msgs))


class StaleOrmQDeleteTest(TestCase):
    """Delete a pending row that the worker already consumed (POST form)."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

    def test_delete_stale_with_completed_task_redirects_to_detail(self):
        task = _create_task(name='already-ran', success=False, result='boom')
        response = self.client.post(
            '/studio/worker/queue/9999/delete/',
            data={'task_id': task.id},
        )
        self.assertRedirects(
            response, f'/studio/worker/task/{task.id}/',
            fetch_redirect_response=False,
        )

    def test_delete_stale_without_task_id_falls_back_to_flash(self):
        response = self.client.post(
            '/studio/worker/queue/9999/delete/', follow=True,
        )
        self.assertRedirects(response, '/studio/worker/')
        msgs = [str(m) for m in get_messages(response.wsgi_request)]
        self.assertTrue(any('already finished' in m.lower() for m in msgs))


class TaskDetailViewTest(TestCase):
    """The new /studio/worker/task/<task_id>/ detail page."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        cls.regular = User.objects.create_user(
            email='user@test.com', password='testpass', is_staff=False,
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

    def test_requires_staff(self):
        task = _create_task()
        self.client.login(email='user@test.com', password='testpass')
        response = self.client.get(f'/studio/worker/task/{task.id}/')
        self.assertEqual(response.status_code, 403)

    def test_anonymous_redirects_to_login(self):
        task = _create_task()
        self.client.logout()
        response = self.client.get(f'/studio/worker/task/{task.id}/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)

    def test_404_for_unknown_task_id(self):
        response = self.client.get(
            '/studio/worker/task/00000000000000000000000000000000/',
        )
        self.assertEqual(response.status_code, 404)

    def test_success_renders_args_kwargs_duration_and_result(self):
        now = timezone.now()
        task = _create_task(
            name='ran-ok',
            args=('source-uuid-very-distinctive', 42),
            kwargs={'batch_id_unique': 'b-1', 'force': True},
            result={'rows_processed_unique_marker': 17, 'ok': True},
            success=True,
            started=now - timedelta(seconds=8, milliseconds=500),
            stopped=now,
        )
        response = self.client.get(f'/studio/worker/task/{task.id}/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'ran-ok')
        # Duration in seconds with millisecond precision.
        self.assertContains(response, '8.500s')
        # Args + kwargs render as pretty-printed Python literals — assert on
        # the marker substrings that don't appear anywhere else on the page.
        self.assertContains(response, 'source-uuid-very-distinctive')
        self.assertContains(response, 'batch_id_unique')
        # Result is the success payload (use a marker that won't collide with
        # page chrome).
        self.assertContains(response, 'rows_processed_unique_marker')
        # Status banner — success path.
        self.assertContains(response, 'data-task-status="success"')
        # Back link to dashboard.
        self.assertContains(response, '/studio/worker/')

    def test_failure_renders_collapsible_traceback(self):
        traceback_text = (
            'Traceback (most recent call last):\n'
            '  File "x.py", line 1, in <module>\n'
            '    raise RuntimeError("nope")\n'
            'RuntimeError: nope'
        )
        task = _create_task(
            name='blew-up', success=False, result=traceback_text,
        )
        response = self.client.get(f'/studio/worker/task/{task.id}/')
        self.assertEqual(response.status_code, 200)
        # Status banner — failed path.
        self.assertContains(response, 'data-task-status="failed"')
        # Collapsible traceback wrapper from #218 (reused via the same
        # data-action / failed-task-trace classes).
        self.assertContains(response, 'data-result-block="traceback"')
        self.assertContains(response, 'data-action="toggle-failed-trace"')
        self.assertContains(response, 'failed-task-trace')
        # Full traceback text is in the response body (inside the <pre>).
        self.assertContains(response, 'RuntimeError: nope')

    def test_failure_with_plain_string_result_skips_collapsible(self):
        """Non-traceback failure messages render inline, not collapsed.

        Avoids burying a one-line ``"boom"`` behind a chevron the operator
        has to click before they can see it.
        """
        task = _create_task(
            name='oops', success=False, result='boom: something went wrong',
        )
        response = self.client.get(f'/studio/worker/task/{task.id}/')
        self.assertNotContains(response, 'data-result-block="traceback"')
        self.assertContains(response, 'boom: something went wrong')


class RecentTasksLinkToDetailTest(TestCase):
    """The Recent Tasks table on /studio/worker/ rows link to detail view."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

    def test_recent_task_row_carries_detail_url(self):
        task = _create_task(name='clickable-row', success=True, result='ok')
        with _fake_dead_clusters():
            response = self.client.get('/studio/worker/')
        self.assertContains(response, 'clickable-row')
        # Row carries the detail URL on a data attribute (used by the row
        # onclick) AND has an inline anchor on the name cell — both routes
        # land the operator on the same view.
        self.assertContains(response, f'/studio/worker/task/{task.id}/')
        self.assertContains(response, 'recent-task-row')


class FragmentEndpointTest(TestCase):
    """`?fragment=pending` returns the pending-tasks partial alone."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

    def test_fragment_returns_only_pending_partial(self):
        ormq, _task_id = _enqueue_ormq(name='still-queued')
        with _fake_dead_clusters():
            response = self.client.get('/studio/worker/?fragment=pending')
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        # Pending row is present.
        self.assertIn('still-queued', body)
        # And we did NOT render the studio sidebar / page chrome.
        self.assertNotIn('id="studio-sidebar"', body)
        self.assertNotIn('Worker Status</h1>', body)

    def test_fragment_empty_queue_returns_placeholder_only(self):
        with _fake_dead_clusters():
            response = self.client.get('/studio/worker/?fragment=pending')
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        # Empty placeholder so the poller can still find the swap target.
        self.assertIn('id="pending-tasks-section"', body)
        # Sidebar must not bleed through.
        self.assertNotIn('id="studio-sidebar"', body)

    def test_fragment_requires_staff(self):
        self.client.logout()
        regular = User.objects.create_user(
            email='regular@test.com', password='testpass', is_staff=False,
        )
        self.client.login(email='regular@test.com', password='testpass')
        response = self.client.get('/studio/worker/?fragment=pending')
        self.assertEqual(response.status_code, 403)
        # Avoid leaving the throwaway user behind.
        regular.delete()


class PendingLinksCarryTaskIdTest(TestCase):
    """Inspect/Delete actions in the pending table carry ``task_id``.

    This is the contract that lets the stale-click recovery (#242) work — the
    OrmQ pk alone is useless once the row is gone.
    """

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

    def test_inspect_link_includes_task_id_query_param(self):
        ormq, task_id = _enqueue_ormq(name='find-me')
        with _fake_dead_clusters():
            response = self.client.get('/studio/worker/')
        self.assertContains(
            response,
            f'/studio/worker/queue/{ormq.pk}/inspect/?task_id={task_id}',
        )

    def test_delete_form_includes_task_id_hidden_input(self):
        ormq, task_id = _enqueue_ormq(name='find-me-2')
        with _fake_dead_clusters():
            response = self.client.get('/studio/worker/')
        # Hidden input is needed because the delete form POSTs without a query
        # string. SimpleNamespace not used here to keep the assertion direct.
        self.assertContains(
            response,
            f'<input type="hidden" name="task_id" value="{task_id}">',
            html=False,
        )
