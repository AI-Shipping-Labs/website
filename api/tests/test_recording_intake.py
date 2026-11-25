"""The recording notification intake is staff-only and independent of transcripts."""

from datetime import timedelta
from unittest.mock import ANY, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import Token
from events.models import RecordingPreparation
from events.tests.test_recording_pipeline_watch import make_event


class RecordingIntakeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user = get_user_model().objects.create_user(email='staff@example.com', is_staff=True)
        cls.token = Token.objects.create(user=user, name='recording-intake-test')

    def setUp(self):
        self.event = make_event()
        self.available = timezone.now() - timedelta(minutes=2)
        self.payload = {
            'event': 'recording.completed', 'meeting_id': self.event.zoom_meeting_id,
            'recording_uuid': 'occurrence/one',
            'start_time': self.event.start_datetime.isoformat(),
            'available_at': self.available.isoformat(),
        }

    def post(self):
        return self.client.post(
            '/api/recording-intake', data=self.payload, content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {self.token.key}',
        )

    def test_staff_authentication_required(self):
        response = self.client.post('/api/recording-intake', self.payload, content_type='application/json')
        self.assertEqual(response.status_code, 401)

    @patch('jobs.tasks.async_task')
    def test_video_receipt_starts_clock_and_enqueues_without_transcript(self, queue):
        response = self.post()
        self.assertEqual(response.status_code, 202)
        self.event.refresh_from_db()
        self.assertEqual(self.event.recording_zoom_available_at, self.available)
        self.assertEqual(self.event.transcript_text, '')
        self.assertFalse(RecordingPreparation.objects.filter(event=self.event).exists())
        queue.assert_called_once_with(
            'events.services.recording_intake.process_recording', self.event.pk,
            'recording.completed', 'occurrence/one',
        )

    @patch('jobs.tasks.async_task')
    def test_transcript_replay_preserves_clock_and_durable_preparation_identity(self, queue):
        self.payload['event'] = 'recording.transcript_completed'
        self.assertEqual(self.post().status_code, 202)
        first = RecordingPreparation.objects.get(event=self.event)
        self.payload['available_at'] = timezone.now().isoformat()
        self.assertEqual(self.post().status_code, 202)
        second = RecordingPreparation.objects.get(event=self.event)
        self.assertEqual(second.pk, first.pk)
        self.assertEqual(second.ready_at, self.available)

    @patch('jobs.tasks.async_task')
    def test_unknown_meeting_is_noop(self, queue):
        self.payload['meeting_id'] = 'different-meeting'
        response = self.post()
        self.assertFalse(response.json()['matched'])
        queue.assert_not_called()

    @patch('jobs.tasks.async_task')
    def test_ambiguous_occurrence_is_rejected_instead_of_wrong_event_upload(self, queue):
        make_event()
        response = self.post()
        self.assertEqual(response.status_code, 409)
        queue.assert_not_called()

    @patch('events.services.recording_pipeline_alerts.deliver_pipeline_alert', return_value=True)
    @patch('jobs.tasks.async_task')
    def test_conflicting_uuid_alerts_and_preserves_canonical_recording(self, queue, alert):
        self.assertEqual(self.post().status_code, 202)
        queue.reset_mock()
        self.payload['recording_uuid'] = 'different-occurrence'
        self.payload['event'] = 'recording.transcript_completed'
        response = self.post()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['code'], 'conflicting_recording_identity')
        self.event.refresh_from_db()
        self.assertEqual(self.event.recording_zoom_uuid, 'occurrence/one')
        self.assertFalse(RecordingPreparation.objects.filter(event=self.event).exists())
        queue.assert_not_called()
        alert.assert_called_once_with(
            self.event, f'Conflicting recording identity (event {self.event.pk})', ANY,
        )

    @patch('jobs.tasks.async_task')
    def test_same_uuid_video_replay_preserves_original_deadline(self, queue):
        self.assertEqual(self.post().status_code, 202)
        self.payload['available_at'] = timezone.now().isoformat()
        self.assertEqual(self.post().status_code, 202)
        self.event.refresh_from_db()
        self.assertEqual(self.event.recording_zoom_available_at, self.available)


    @patch('jobs.tasks.async_task')
    def test_missing_provider_timestamp_cannot_start_a_receipt_time_deadline(self, queue):
        del self.payload['available_at']
        response = self.post()
        self.assertEqual(response.status_code, 422)
        self.event.refresh_from_db()
        self.assertIsNone(self.event.recording_zoom_available_at)
        queue.assert_not_called()

    @patch('jobs.tasks.async_task')
    def test_null_malformed_and_future_provider_timestamps_are_rejected(self, queue):
        future = (timezone.now() + timedelta(days=1)).isoformat()
        for value in (None, 'invalid-time', future):
            with self.subTest(available_at=value):
                self.payload['available_at'] = value
                self.assertEqual(self.post().status_code, 422)
        queue.assert_not_called()
