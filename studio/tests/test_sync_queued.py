"""Tests for the ``queued`` sync state and watchdog (issue #274).

Covers:
- Trigger views set ``last_sync_status='queued'`` and create a queued
  SyncLog row when enqueueing.
- The worker (``sync_content_source``) transitions queued→running by
  UPDATING the existing row, not creating a duplicate.
- The dashboard watchdog flips queued > N min and running > M min rows
  to ``failed`` and syncs the corresponding ContentSource status.
- Fresh queued/running rows are NOT auto-failed.
- Both the dashboard view and the JSON status polling endpoint run the
  watchdog.
- The dashboard renders a blue ``queued`` pill with the documented
  tooltip.
"""

import datetime
import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from integrations.models import ContentSource, SyncLog
from studio.views.sync import (
    WATCHDOG_QUEUED_ERROR,
    WATCHDOG_RUNNING_ERROR,
)

User = get_user_model()


# ============================================================================
# Trigger views: queued state on enqueue
# ============================================================================


class SyncTriggerSetsQueuedStateTest(TestCase):
    """Issue #274: trigger views must set last_sync_status='queued' AND
    create a SyncLog row at status='queued' after a successful async_task
    enqueue. Otherwise a previous worker death leaves the dashboard
    visibly stuck in 'running' even after the operator clicks again.
    """

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email='staff@test.com', password='testpass')
        self.source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/blog',
            content_type='article',
        )

    @patch('django_q.tasks.async_task')
    def test_trigger_sets_source_status_to_queued(self, mock_async):
        self.client.post(f'/studio/sync/{self.source.pk}/trigger/')
        self.source.refresh_from_db()
        self.assertEqual(self.source.last_sync_status, 'queued')

    @patch('django_q.tasks.async_task')
    def test_trigger_creates_queued_synclog(self, mock_async):
        self.client.post(f'/studio/sync/{self.source.pk}/trigger/')
        log = SyncLog.objects.get(source=self.source)
        self.assertEqual(log.status, 'queued')

    @patch('django_q.tasks.async_task')
    def test_trigger_overwrites_stale_running_status(self, mock_async):
        """Previous worker death left the source at 'running'. Clicking
        Sync now must visibly move it to 'queued' so the operator sees
        their click took effect.
        """
        self.source.last_sync_status = 'running'
        self.source.save(update_fields=['last_sync_status', 'updated_at'])
        self.client.post(f'/studio/sync/{self.source.pk}/trigger/')
        self.source.refresh_from_db()
        self.assertEqual(self.source.last_sync_status, 'queued')

    @patch('django_q.tasks.async_task', side_effect=Exception('queue error'))
    def test_trigger_does_not_set_queued_when_enqueue_fails(self, mock_async):
        """If the enqueue itself raises, we must NOT lie about the row
        being queued — there's nothing in the queue.
        """
        self.client.post(f'/studio/sync/{self.source.pk}/trigger/')
        self.source.refresh_from_db()
        self.assertNotEqual(self.source.last_sync_status, 'queued')
        self.assertFalse(
            SyncLog.objects.filter(source=self.source, status='queued').exists()
        )


class SyncRepoTriggerSetsQueuedStateTest(TestCase):
    """Issue #274: per-repo fan-out trigger sets queued for every source."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email='staff@test.com', password='testpass')

    @patch('django_q.tasks.async_task')
    def test_repo_trigger_sets_queued_for_each_source(self, mock_async):
        article = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content', content_type='article',
        )
        course = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='course', content_path='courses/',
        )
        self.client.post('/studio/sync/AI-Shipping-Labs/content/trigger-repo/')
        article.refresh_from_db()
        course.refresh_from_db()
        self.assertEqual(article.last_sync_status, 'queued')
        self.assertEqual(course.last_sync_status, 'queued')

    @patch('django_q.tasks.async_task')
    def test_repo_trigger_creates_queued_synclog_per_source(self, mock_async):
        article = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content', content_type='article',
        )
        course = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='course', content_path='courses/',
        )
        self.client.post('/studio/sync/AI-Shipping-Labs/content/trigger-repo/')
        self.assertEqual(
            SyncLog.objects.filter(source=article, status='queued').count(), 1,
        )
        self.assertEqual(
            SyncLog.objects.filter(source=course, status='queued').count(), 1,
        )

    @patch('django_q.tasks.async_task')
    def test_repo_trigger_queued_rows_share_batch_id(self, mock_async):
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content', content_type='article',
        )
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='course', content_path='courses/',
        )
        self.client.post('/studio/sync/AI-Shipping-Labs/content/trigger-repo/')
        queued = SyncLog.objects.filter(status='queued')
        batch_ids = {log.batch_id for log in queued}
        self.assertEqual(len(batch_ids), 1)
        self.assertIsNotNone(next(iter(batch_ids)))


class SyncAllSetsQueuedStateTest(TestCase):
    """Issue #274: ``Sync All`` sets queued for every configured source."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email='staff@test.com', password='testpass')

    @patch('django_q.tasks.async_task')
    def test_sync_all_sets_queued_for_all_sources(self, mock_async):
        a = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/blog', content_type='article',
        )
        b = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='project', content_path='projects/',
        )
        self.client.post('/studio/sync/all/')
        a.refresh_from_db()
        b.refresh_from_db()
        self.assertEqual(a.last_sync_status, 'queued')
        self.assertEqual(b.last_sync_status, 'queued')

    @patch('django_q.tasks.async_task')
    def test_sync_all_creates_one_queued_synclog_per_source(self, mock_async):
        a = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/blog', content_type='article',
        )
        b = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='project', content_path='projects/',
        )
        self.client.post('/studio/sync/all/')
        self.assertEqual(
            SyncLog.objects.filter(source=a, status='queued').count(), 1,
        )
        self.assertEqual(
            SyncLog.objects.filter(source=b, status='queued').count(), 1,
        )


# ============================================================================
# Worker pickup: queued → running, no duplicate row
# ============================================================================


class WorkerQueuedToRunningTransitionTest(TestCase):
    """Issue #274: when the worker picks up a task that the trigger view
    already enqueued at status='queued', it must UPDATE that row to
    'running' rather than create a duplicate.
    """

    def setUp(self):
        self.source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/blog', content_type='article',
        )

    def _run_worker(self, **kwargs):
        # Use repo_dir to bypass clone+lock; this exercises the queued→running
        # transition without needing a real git repo.
        from integrations.services.github import sync_content_source
        # Build a tiny fake on-disk repo so the article syncer has nothing
        # to do but a no-op pass.
        import tempfile, os
        d = tempfile.mkdtemp(prefix='gh-test-')
        try:
            return sync_content_source(self.source, repo_dir=d, **kwargs)
        finally:
            import shutil
            shutil.rmtree(d, ignore_errors=True)

    def test_worker_updates_existing_queued_row(self):
        queued = SyncLog.objects.create(source=self.source, status='queued')
        log = self._run_worker()
        # Same row, same PK — worker did NOT create a new SyncLog.
        self.assertEqual(log.pk, queued.pk)

    def test_worker_does_not_create_duplicate_synclog(self):
        SyncLog.objects.create(source=self.source, status='queued')
        self._run_worker()
        # Exactly one SyncLog total for this source: the original queued
        # one (now in some terminal state).
        self.assertEqual(
            SyncLog.objects.filter(source=self.source).count(), 1,
        )

    def test_worker_creates_synclog_when_no_queued_row_exists(self):
        """Direct CLI invocation (manage.py sync_content) bypasses the
        trigger view, so there's no queued row to update. Worker must
        still create one — the existing CLI behaviour is preserved.
        """
        self._run_worker()
        self.assertEqual(
            SyncLog.objects.filter(source=self.source).count(), 1,
        )

    def test_worker_overwrites_source_queued_status_with_running(self):
        SyncLog.objects.create(source=self.source, status='queued')
        self.source.last_sync_status = 'queued'
        self.source.save(update_fields=['last_sync_status'])
        self._run_worker()
        self.source.refresh_from_db()
        # After a successful run, source goes to 'success' (or similar
        # terminal state). What matters here is that it's NOT stuck at
        # 'queued' — the worker promoted it past that.
        self.assertNotEqual(self.source.last_sync_status, 'queued')

    def test_worker_carries_batch_id_when_queued_row_lacks_one(self):
        """A direct ``async_task(..., batch_id=X)`` call (without going
        through the trigger view) lands at the worker with a batch_id.
        If we picked up an old queued row without one, propagate.
        """
        import uuid as _uuid
        queued = SyncLog.objects.create(source=self.source, status='queued')
        self.assertIsNone(queued.batch_id)
        bid = _uuid.uuid4()
        self._run_worker(batch_id=bid)
        queued.refresh_from_db()
        self.assertEqual(queued.batch_id, bid)


# ============================================================================
# Watchdog
# ============================================================================


class WatchdogQueuedTimeoutTest(TestCase):
    """Issue #274: queued > N min must auto-fail."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email='staff@test.com', password='testpass')
        self.source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
            last_sync_status='queued',
        )

    def _make_old_queued_log(self, age_minutes):
        log = SyncLog.objects.create(source=self.source, status='queued')
        SyncLog.objects.filter(pk=log.pk).update(
            started_at=timezone.now() - datetime.timedelta(minutes=age_minutes),
        )
        log.refresh_from_db()
        return log

    def test_old_queued_synclog_flipped_to_failed(self):
        log = self._make_old_queued_log(age_minutes=15)
        self.client.get('/studio/sync/')
        log.refresh_from_db()
        self.assertEqual(log.status, 'failed')

    def test_old_queued_synclog_gets_documented_error_message(self):
        log = self._make_old_queued_log(age_minutes=15)
        self.client.get('/studio/sync/')
        log.refresh_from_db()
        # Must include the watchdog's reason so operators triaging the
        # SyncLog can tell why it failed.
        errors_text = ' '.join(e.get('error', '') for e in (log.errors or []))
        expected = WATCHDOG_QUEUED_ERROR.format(minutes=10)
        self.assertIn(expected, errors_text)

    def test_old_queued_source_status_synced_to_failed(self):
        self._make_old_queued_log(age_minutes=15)
        self.client.get('/studio/sync/')
        self.source.refresh_from_db()
        self.assertEqual(self.source.last_sync_status, 'failed')

    def test_fresh_queued_synclog_NOT_failed(self):
        """No false-positives: a 5-minute-old queued row stays queued."""
        log = self._make_old_queued_log(age_minutes=5)
        self.client.get('/studio/sync/')
        log.refresh_from_db()
        self.assertEqual(log.status, 'queued')

    def test_fresh_queued_source_status_NOT_changed(self):
        self._make_old_queued_log(age_minutes=5)
        self.client.get('/studio/sync/')
        self.source.refresh_from_db()
        self.assertEqual(self.source.last_sync_status, 'queued')

    def test_old_queued_synclog_finished_at_set(self):
        log = self._make_old_queued_log(age_minutes=15)
        self.client.get('/studio/sync/')
        log.refresh_from_db()
        self.assertIsNotNone(log.finished_at)


class WatchdogRunningTimeoutTest(TestCase):
    """Issue #274: running > M min must auto-fail."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email='staff@test.com', password='testpass')
        self.source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
            last_sync_status='running',
        )

    def _make_old_running_log(self, age_minutes):
        log = SyncLog.objects.create(source=self.source, status='running')
        SyncLog.objects.filter(pk=log.pk).update(
            started_at=timezone.now() - datetime.timedelta(minutes=age_minutes),
        )
        log.refresh_from_db()
        return log

    def test_old_running_synclog_flipped_to_failed(self):
        log = self._make_old_running_log(age_minutes=45)
        self.client.get('/studio/sync/')
        log.refresh_from_db()
        self.assertEqual(log.status, 'failed')

    def test_old_running_synclog_gets_documented_error_message(self):
        log = self._make_old_running_log(age_minutes=45)
        self.client.get('/studio/sync/')
        log.refresh_from_db()
        errors_text = ' '.join(e.get('error', '') for e in (log.errors or []))
        expected = WATCHDOG_RUNNING_ERROR.format(minutes=30)
        self.assertIn(expected, errors_text)

    def test_old_running_source_status_synced_to_failed(self):
        self._make_old_running_log(age_minutes=45)
        self.client.get('/studio/sync/')
        self.source.refresh_from_db()
        self.assertEqual(self.source.last_sync_status, 'failed')

    def test_fresh_running_synclog_NOT_failed(self):
        """A 15-minute-old running row stays running."""
        log = self._make_old_running_log(age_minutes=15)
        self.client.get('/studio/sync/')
        log.refresh_from_db()
        self.assertEqual(log.status, 'running')

    def test_fresh_running_source_status_NOT_changed(self):
        self._make_old_running_log(age_minutes=15)
        self.client.get('/studio/sync/')
        self.source.refresh_from_db()
        self.assertEqual(self.source.last_sync_status, 'running')


class WatchdogStatusEndpointTest(TestCase):
    """The polling endpoint runs the watchdog too (issue #274)."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email='staff@test.com', password='testpass')
        self.source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
            last_sync_status='queued',
        )

    def test_status_endpoint_runs_watchdog_on_old_queued(self):
        log = SyncLog.objects.create(source=self.source, status='queued')
        SyncLog.objects.filter(pk=log.pk).update(
            started_at=timezone.now() - datetime.timedelta(minutes=15),
        )
        response = self.client.get(f'/studio/sync/{self.source.pk}/status/')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(data['last_sync_status'], 'failed')
        log.refresh_from_db()
        self.assertEqual(log.status, 'failed')

    def test_status_endpoint_does_not_fail_fresh_queued(self):
        log = SyncLog.objects.create(source=self.source, status='queued')
        SyncLog.objects.filter(pk=log.pk).update(
            started_at=timezone.now() - datetime.timedelta(minutes=3),
        )
        response = self.client.get(f'/studio/sync/{self.source.pk}/status/')
        data = json.loads(response.content)
        self.assertEqual(data['last_sync_status'], 'queued')
        log.refresh_from_db()
        self.assertEqual(log.status, 'queued')


class WatchdogConfigurableThresholdsTest(TestCase):
    """Both thresholds must be configurable via Django settings/env."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email='staff@test.com', password='testpass')
        self.source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
            last_sync_status='queued',
        )

    @override_settings(SYNC_QUEUED_THRESHOLD_MINUTES=5)
    def test_custom_queued_threshold_respected(self):
        # 7 minutes — would be safe at the 10-minute default but is
        # stuck at the overridden 5-minute threshold.
        log = SyncLog.objects.create(source=self.source, status='queued')
        SyncLog.objects.filter(pk=log.pk).update(
            started_at=timezone.now() - datetime.timedelta(minutes=7),
        )
        self.client.get('/studio/sync/')
        log.refresh_from_db()
        self.assertEqual(log.status, 'failed')

    @override_settings(SYNC_RUNNING_THRESHOLD_MINUTES=10)
    def test_custom_running_threshold_respected(self):
        self.source.last_sync_status = 'running'
        self.source.save(update_fields=['last_sync_status', 'updated_at'])
        log = SyncLog.objects.create(source=self.source, status='running')
        SyncLog.objects.filter(pk=log.pk).update(
            started_at=timezone.now() - datetime.timedelta(minutes=15),
        )
        self.client.get('/studio/sync/')
        log.refresh_from_db()
        self.assertEqual(log.status, 'failed')

    @override_settings(SYNC_QUEUED_THRESHOLD_MINUTES=5)
    def test_watchdog_error_message_uses_configured_threshold(self):
        log = SyncLog.objects.create(source=self.source, status='queued')
        SyncLog.objects.filter(pk=log.pk).update(
            started_at=timezone.now() - datetime.timedelta(minutes=7),
        )
        self.client.get('/studio/sync/')
        log.refresh_from_db()
        errors_text = ' '.join(e.get('error', '') for e in (log.errors or []))
        # Must reference the actual configured threshold, not a magic
        # number, so operators can correlate with their env config.
        self.assertIn('5 minutes', errors_text)


# ============================================================================
# Dashboard rendering of the queued state
# ============================================================================


class DashboardRendersQueuedPillTest(TestCase):
    """The dashboard renders a blue ``queued`` pill with the documented
    tooltip (issue #274). Operators must be able to tell at a glance
    that the click landed but the worker hasn't started yet.
    """

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email='staff@test.com', password='testpass')
        self.source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
            last_sync_status='queued',
        )
        # Fresh (1 minute old) so the watchdog doesn't auto-fail it.
        log = SyncLog.objects.create(source=self.source, status='queued')
        SyncLog.objects.filter(pk=log.pk).update(
            started_at=timezone.now() - datetime.timedelta(minutes=1),
        )

    def test_dashboard_renders_queued_label(self):
        response = self.client.get('/studio/sync/')
        self.assertContains(response, '>queued<')

    def test_dashboard_queued_pill_uses_blue_classes(self):
        """Blue is distinct from amber/yellow ``partial`` and green
        ``success`` and red ``failed`` so the pill carries meaning at a
        glance.
        """
        response = self.client.get('/studio/sync/')
        body = response.content.decode()
        idx = body.find('>queued<')
        self.assertGreater(idx, -1)
        snippet = body[max(0, idx - 400):idx]
        # The pill background is in the blue family.
        self.assertIn('blue', snippet)
        # And NOT amber/yellow (would conflict with partial) or green.
        self.assertNotIn('bg-yellow-500/20', snippet)
        self.assertNotIn('bg-green-500/20', snippet)
        self.assertNotIn('bg-red-500/20', snippet)

    def test_dashboard_queued_pill_has_tooltip(self):
        """The pill must include the documented tooltip text so hovering
        operators see why the row is in this state.
        """
        response = self.client.get('/studio/sync/')
        self.assertContains(
            response, 'Waiting for worker to pick up the task',
        )

    def test_dashboard_queued_keeps_card_in_any_running(self):
        """A queued source keeps the auto-refresh poller ticking — same
        as a running one — so the operator sees the row flip to running
        as soon as the worker picks up.
        """
        response = self.client.get('/studio/sync/')
        # The polling section data attribute must signal "yes, keep
        # polling" while a queued row is in flight.
        self.assertContains(response, 'data-any-running="true"')

    def test_dashboard_sync_now_button_still_enabled_when_queued(self):
        """Spec: queued state does NOT disable the Sync now button — the
        operator can re-queue (idempotent) or hit Force resync.
        """
        response = self.client.get('/studio/sync/')
        body = response.content.decode()
        # The Sync now form/button must be present and not carry a
        # disabled attribute on the button.
        self.assertIn('Sync now', body)
        # No disabled attribute on the sync button itself.
        idx = body.find('Sync now')
        self.assertGreater(idx, -1)
        snippet = body[max(0, idx - 400):idx + 50]
        self.assertNotIn('disabled', snippet)


# ============================================================================
# End-to-end state machine
# ============================================================================


class QueuedToRunningToSuccessFlowTest(TestCase):
    """End-to-end: trigger creates queued; worker promotes to running and
    then to success — all on the SAME SyncLog row (no duplicates).
    """

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email='staff@test.com', password='testpass')
        self.source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/blog', content_type='article',
        )

    @patch('django_q.tasks.async_task')
    def test_full_state_machine_no_duplicate_synclog(self, mock_async):
        # Step 1: operator clicks Sync now → queued.
        self.client.post(f'/studio/sync/{self.source.pk}/trigger/')
        self.assertEqual(
            SyncLog.objects.filter(source=self.source).count(), 1,
        )
        queued_log = SyncLog.objects.get(source=self.source)
        self.assertEqual(queued_log.status, 'queued')

        # Step 2: worker picks up → running, same row updated in place.
        from integrations.services.github import sync_content_source
        import tempfile, shutil
        tmp = tempfile.mkdtemp(prefix='gh-test-')
        try:
            log = sync_content_source(self.source, repo_dir=tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

        # Same row.
        self.assertEqual(log.pk, queued_log.pk)
        # Still only one SyncLog row total — no duplicate.
        self.assertEqual(
            SyncLog.objects.filter(source=self.source).count(), 1,
        )
        # And the row is no longer queued or running — terminal state.
        log.refresh_from_db()
        self.assertNotIn(log.status, ('queued', 'running'))
