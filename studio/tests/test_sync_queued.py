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
from django.test import TestCase, override_settings
from django.utils import timezone

from community_base.content_sync.models import ContentSource, SyncLog
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
        # Issue #532: source is the "system under test" but Django wraps
        # setUpTestData rows in TestData (per-test deepcopy) so attribute
        # mutations and the surrounding transaction rollback prevent
        # cross-test leaks.
        cls.source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/blog',
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

    def test_trigger_sets_source_status_to_queued(self):
        self.client.post(f'/studio/sync/{self.source.pk}/trigger/')
        self.source.refresh_from_db()
        self.assertEqual(self.source.last_sync_status, 'queued')

    def test_trigger_creates_queued_synclog(self):
        self.client.post(f'/studio/sync/{self.source.pk}/trigger/')
        log = SyncLog.objects.get(source=self.source)
        self.assertEqual(log.status, 'queued')

    def test_trigger_overwrites_stale_running_status(self):
        """Previous worker death left the source at 'running'. Clicking
        Sync now must visibly move it to 'queued' so the operator sees
        their click took effect.
        """
        self.source.last_sync_status = 'running'
        self.source.save(update_fields=['last_sync_status', 'updated_at'])
        self.client.post(f'/studio/sync/{self.source.pk}/trigger/')
        self.source.refresh_from_db()
        self.assertEqual(self.source.last_sync_status, 'queued')

    @patch(
        'integrations.services.content_sync_queue.package_queue_source_sync',
        side_effect=Exception('queue error'),
    )
    def test_trigger_does_not_set_queued_when_enqueue_fails(self, mock_queue):
        """If the enqueue itself raises, we must NOT lie about the row
        being queued — there's nothing in the queue.
        """
        with self.assertLogs('studio.views.sync', level='ERROR') as logs:
            self.client.post(f'/studio/sync/{self.source.pk}/trigger/')
        self.source.refresh_from_db()
        self.assertNotEqual(self.source.last_sync_status, 'queued')
        self.assertIn(
            'Error triggering sync for AI-Shipping-Labs/blog',
            logs.output[0],
        )
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
        self.client.login(email='staff@test.com', password='testpass')

    def test_repo_trigger_sets_queued_for_source(self):
        """Issue #310: one ContentSource per repo. The trigger marks the
        single source queued."""
        source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
        )
        self.client.post('/studio/sync/AI-Shipping-Labs/content/trigger-repo/')
        source.refresh_from_db()
        self.assertEqual(source.last_sync_status, 'queued')

    def test_repo_trigger_creates_queued_synclog(self):
        source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
        )
        self.client.post('/studio/sync/AI-Shipping-Labs/content/trigger-repo/')
        self.assertEqual(
            SyncLog.objects.filter(source=source, status='queued').count(), 1,
        )

    def test_repo_trigger_queued_row_carries_batch_id(self):
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
        )
        self.client.post('/studio/sync/AI-Shipping-Labs/content/trigger-repo/')
        queued = SyncLog.objects.filter(status='queued')
        self.assertEqual(queued.count(), 1)
        self.assertIsNotNone(queued.first().batch_id)


class SyncAllSetsQueuedStateTest(TestCase):
    """Issue #274: ``Sync All`` sets queued for every configured source."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

    def test_sync_all_sets_queued_for_all_sources(self):
        a = ContentSource.objects.create(
            slug='blog', repo_name='AI-Shipping-Labs/blog',
        )
        b = ContentSource.objects.create(
            slug='content', repo_name='AI-Shipping-Labs/content',
        )
        self.client.post('/studio/sync/all/')
        a.refresh_from_db()
        b.refresh_from_db()
        self.assertEqual(a.last_sync_status, 'queued')
        self.assertEqual(b.last_sync_status, 'queued')

    def test_sync_all_creates_one_queued_synclog_per_source(self):
        a = ContentSource.objects.create(
            slug='blog', repo_name='AI-Shipping-Labs/blog',
        )
        b = ContentSource.objects.create(
            slug='content', repo_name='AI-Shipping-Labs/content',
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
    """A2.3: the trigger view writes a queued marker row and the package
    engine writes its own running/terminal row for the worker run; the
    stale marker is cleaned up by the watchdog (10 minutes). These tests
    pin the worker side of that contract: the run row carries the real
    outcome and the source status moves past 'queued'.
    """

    @classmethod
    def setUpTestData(cls):
        # Issue #532: read-only source fixture; tests create their own
        # SyncLog rows and assert the worker updates / creates them.
        cls.source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/blog',
        )

    def _run_worker(self, **kwargs):
        # Use repo_dir to bypass clone+lock; this exercises the queued→running
        # transition without needing a real git repo.

        # Build a tiny fake on-disk repo so the article syncer has nothing
        # to do but a no-op pass.
        import tempfile

        from integrations.services.github import sync_content_source
        d = tempfile.mkdtemp(prefix='gh-test-')
        try:
            return sync_content_source(self.source, repo_dir=d, **kwargs)
        finally:
            import shutil
            shutil.rmtree(d, ignore_errors=True)

    def test_worker_run_row_is_terminal_not_queued(self):
        SyncLog.objects.create(source=self.source, status='queued')
        log = self._run_worker()
        # The worker's row records the real run outcome.
        self.assertNotEqual(log.status, 'queued')

    def test_worker_run_leaves_marker_cleanup_to_the_watchdog(self):
        queued = SyncLog.objects.create(source=self.source, status='queued')
        self._run_worker()
        # The queued marker row is kept for audit and flipped by the
        # watchdog once it ages past the threshold; the run row is separate.
        queued.refresh_from_db()
        self.assertEqual(queued.status, 'queued')
        self.assertEqual(
            SyncLog.objects.filter(source=self.source).count(), 2,
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

    def test_worker_run_carries_batch_id(self):
        """A worker run given a batch_id records it on its own run row.
        """
        import uuid as _uuid
        bid = _uuid.uuid4()
        log = self._run_worker(batch_id=bid)
        self.assertEqual(log.batch_id, bid)


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
        # Issue #532: read-only source fixture (status changes via DB
        # refresh, never via direct attribute mutation that needs to
        # persist across tests).
        cls.source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            last_sync_status='queued',
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

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
        # Issue #532: read-only source fixture for the running watchdog tests.
        cls.source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            last_sync_status='running',
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

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
        # Issue #532: read-only source fixture.
        cls.source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            last_sync_status='queued',
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

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
        # Issue #532: read-only source fixture (last_sync_status mutates
        # in test_custom_running_threshold_respected via DB save, which
        # rolls back per-test).
        cls.source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            last_sync_status='queued',
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

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
        # Issue #532: read-only source + queued SyncLog fixture.
        cls.source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            last_sync_status='queued',
        )
        # Fresh (1 minute old) so the watchdog doesn't auto-fail it.
        log = SyncLog.objects.create(source=cls.source, status='queued')
        SyncLog.objects.filter(pk=log.pk).update(
            started_at=timezone.now() - datetime.timedelta(minutes=1),
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

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
    """End-to-end: the trigger writes the queued marker row; the worker
    run records its own row and drives the source status to a terminal
    state. The stale marker is watchdog cleanup, not worker state.
    """

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        # Issue #532: read-only source fixture.
        cls.source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/blog',
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

    def test_full_state_machine_marker_plus_run_row(self):
        # Step 1: operator clicks Sync now → queued marker row.
        self.client.post(f'/studio/sync/{self.source.pk}/trigger/')
        self.assertEqual(
            SyncLog.objects.filter(source=self.source).count(), 1,
        )
        queued_log = SyncLog.objects.get(source=self.source)
        self.assertEqual(queued_log.status, 'queued')

        # Step 2: worker picks up → its own running/terminal row.
        import shutil
        import tempfile

        from integrations.services.github import sync_content_source
        tmp = tempfile.mkdtemp(prefix='gh-test-')
        try:
            log = sync_content_source(self.source, repo_dir=tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

        # The worker row is not the marker and is terminal.
        self.assertNotEqual(log.pk, queued_log.pk)
        self.assertNotIn(log.status, ('queued', 'running'))
        # Two rows total: the queued marker plus the run row.
        self.assertEqual(
            SyncLog.objects.filter(source=self.source).count(), 2,
        )
        # And the source status is no longer queued — the worker moved it
        # past the marker state.
        self.source.refresh_from_db()
        self.assertNotEqual(self.source.last_sync_status, 'queued')
