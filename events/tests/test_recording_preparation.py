"""Hub workshop preparation receipts, deadlines, retries, and draft-only inputs."""

from datetime import timedelta
from unittest.mock import Mock, patch

from django.test import TestCase
from django.utils import timezone

from events.models import RecordingPreparation
from events.services.recording_preparation import (
    PreparationError,
    dispatch_preparation,
    reconcile_preparations,
    refresh_preparation,
)
from events.tests.test_recording_pipeline_watch import make_event

SERVICE = 'events.services.recording_preparation'
ALERT = 'events.services.recording_pipeline_alerts.deliver_pipeline_alert'


class RecordingPreparationTests(TestCase):
    def setUp(self):
        self.event = make_event()
        self.ready = timezone.now() - timedelta(minutes=10)
        self.preparation = RecordingPreparation.objects.create(event=self.event, ready_at=self.ready)
        connection = patch(f'{SERVICE}._connection', return_value=(
            'https://hub.example.com/webhooks/route', {'X-Webhook-Token': 'test'},
        ))
        connection.start()
        self.addCleanup(connection.stop)

    @patch(f'{SERVICE}.requests.post')
    def test_dispatch_is_idempotent_and_references_authenticated_source(self, post):
        post.return_value = Mock(status_code=202)
        post.return_value.json.return_value = {'delivery_id': 'delivery-one', 'status': 'pending'}
        self.assertEqual(dispatch_preparation(self.event.pk), 'delivery-one')
        self.assertEqual(dispatch_preparation(self.event.pk), 'delivery-one')
        post.assert_called_once()
        request = post.call_args.kwargs
        self.assertTrue(request['json']['draft_only'])
        self.assertEqual(request['json']['outputs'], ['video_chapters', 'workshops_raw_summary'])
        self.assertTrue(request['json']['source_url'].endswith(f'/{self.event.slug}/preparation-source'))
        self.assertEqual(request['headers']['X-Delivery-Id'], f'aisl-recording-preparation-{self.event.pk}')
        self.assertNotIn('transcript_s3_url', request['json'])

    @patch(f'{SERVICE}.requests.get')
    def test_queued_receipt_never_counts_as_finished(self, get):
        self.preparation.delivery_id = 'delivery-one'
        self.preparation.save()
        get.return_value = Mock(status_code=200)
        get.return_value.json.return_value = {'delivery_id': 'delivery-one', 'status': 'queued'}
        refresh_preparation(self.preparation)
        self.preparation.refresh_from_db()
        self.assertEqual(self.preparation.status, 'queued')
        self.assertIsNone(self.preparation.started_at)
        self.assertIsNone(self.preparation.finished_at)

    @patch(f'{SERVICE}.requests.get')
    def test_finished_without_artifact_proof_is_rejected(self, get):
        get.return_value = Mock(status_code=200)
        get.return_value.json.return_value = {'delivery_id': '', 'status': 'finished'}
        with self.assertRaisesMessage(PreparationError, 'hub_incomplete_finish_proof'):
            refresh_preparation(self.preparation)
        self.preparation.refresh_from_db()
        self.assertEqual(self.preparation.status, 'pending')

    @patch(ALERT, return_value=True)
    @patch(f'{SERVICE}.refresh_preparation')
    @patch(f'{SERVICE}.dispatch_preparation', side_effect=PreparationError('offline'))
    def test_dispatch_failure_alerts_after_start_bound_and_retains_original_clock(self, dispatch, refresh, alert):
        reconcile_preparations()
        self.preparation.refresh_from_db()
        self.assertEqual(self.preparation.ready_at, self.ready)
        self.assertIsNotNone(self.preparation.alerted_at)
        self.assertEqual(self.preparation.status, 'pending')
        self.assertIn('start within 5 minutes', alert.call_args.args[2])
        refresh.assert_not_called()

    @patch(ALERT, return_value=False)
    @patch(f'{SERVICE}.refresh_preparation')
    def test_running_job_missing_finish_alert_retries_until_staff_route_accepts(self, refresh, alert):
        self.preparation.ready_at = timezone.now() - timedelta(minutes=65)
        self.preparation.started_at = self.preparation.ready_at + timedelta(minutes=1)
        self.preparation.delivery_id = 'delivery-one'
        self.preparation.status = 'running'
        self.preparation.save()
        reconcile_preparations()
        self.preparation.refresh_from_db()
        self.assertIsNone(self.preparation.alerted_at)
        alert.return_value = True
        reconcile_preparations()
        self.preparation.refresh_from_db()
        self.assertIsNotNone(self.preparation.alerted_at)
        self.assertEqual(alert.call_count, 2)

    @patch(ALERT, return_value=True)
    def test_late_success_still_alerts_about_missed_bound(self, alert):
        self.preparation.started_at = self.ready + timedelta(minutes=7)
        self.preparation.finished_at = self.ready + timedelta(minutes=9)
        self.preparation.status = 'finished'
        self.preparation.delivery_id = 'delivery-one'
        self.preparation.save()
        reconcile_preparations()
        alert.assert_called_once()

    @patch(ALERT, return_value=True)
    @patch(f'{SERVICE}.refresh_preparation')
    @patch(f'{SERVICE}.dispatch_preparation', side_effect=PreparationError('offline'))
    def test_start_and_finish_deadlines_have_independent_alerts(self, dispatch, refresh, alert):
        reconcile_preparations()
        self.preparation.refresh_from_db()
        self.assertIsNotNone(self.preparation.start_alerted_at)
        self.assertIsNone(self.preparation.finish_alerted_at)
        self.preparation.ready_at = timezone.now() - timedelta(minutes=65)
        self.preparation.save()
        reconcile_preparations()
        self.preparation.refresh_from_db()
        self.assertIsNotNone(self.preparation.finish_alerted_at)
        self.assertEqual(alert.call_count, 2)

    @patch(ALERT, return_value=True)
    @patch(f'{SERVICE}.dispatch_preparation', side_effect=PreparationError('offline'))
    def test_completed_rows_do_not_starve_later_pending_work(self, dispatch, alert):
        self.preparation.ready_at = timezone.now() - timedelta(minutes=80)
        self.preparation.save()
        for index in range(21):
            event = make_event()
            ready = self.ready - timedelta(days=1)
            RecordingPreparation.objects.create(
                event=event, ready_at=ready, status='finished',
                started_at=ready + timedelta(minutes=1),
                finished_at=ready + timedelta(minutes=2), delivery_id=f'finished-{index}',
            )
        reconcile_preparations()
        dispatch.assert_called_once_with(self.event.pk)
        alert.assert_called_once()

    @patch('events.services.recap_draft.enqueue_recap_draft_task')
    @patch(f'{SERVICE}.dispatch_preparation', return_value='new-delivery')
    def test_automatic_transcript_completion_never_calls_legacy_recap_publication(self, dispatch, legacy):
        from jobs.tasks.recording_transcript import _after_transcript

        self.preparation.delete()
        result = _after_transcript(self.event, False)
        self.assertEqual(result['status'], 'hub_draft_queued')
        self.assertTrue(RecordingPreparation.objects.filter(event=self.event).exists())
        legacy.assert_not_called()


    @patch(ALERT, return_value=True)
    def test_already_alerted_active_jobs_cannot_hide_new_deadline_breach(self, alert):
        from events.services.recording_preparation import check_preparation_deadlines

        for index in range(21):
            event = make_event()
            ready = self.ready - timedelta(days=1)
            RecordingPreparation.objects.create(
                event=event, ready_at=ready, status='running', delivery_id=f'active-{index}',
                start_alerted_at=self.ready, finish_alerted_at=self.ready, alerted_at=self.ready,
            )
        check_preparation_deadlines(timezone.now())
        alert.assert_called_once()
        self.assertEqual(alert.call_args.args[0].pk, self.event.pk)

    @patch(ALERT, return_value=True)
    def test_unknown_start_gets_observation_tolerance_without_weakening_real_deadline(self, alert):
        from events.services.recording_preparation import check_preparation_deadlines

        self.preparation.ready_at = timezone.now() - timedelta(minutes=6)
        self.preparation.save()
        check_preparation_deadlines(timezone.now())
        alert.assert_not_called()
        self.preparation.started_at = self.preparation.ready_at + timedelta(minutes=6)
        self.preparation.save()
        check_preparation_deadlines(timezone.now())
        alert.assert_called_once()
