"""API contract for Zoom recording upload retry (issue #1505)."""

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import Token
from events.models import Event

User = get_user_model()


class EventRecordingUploadApiTest(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            email='recording-api-staff@example.com',
            password='pw',
            is_staff=True,
        )
        self.staff_token = Token.objects.create(user=self.staff, name='recording')
        start = timezone.now() - timedelta(hours=3)
        self.event = Event.objects.create(
            title='Recording API event',
            slug='recording-api-event',
            start_datetime=start,
            end_datetime=start + timedelta(hours=1),
            status='completed',
            origin='github',
            source_repo='AI-Shipping-Labs/content',
            source_path='events/recording.md',
            recording_zoom_download_url='https://zoom.us/rec/download/api',
            recording_upload_enqueued_at=timezone.now() - timedelta(minutes=25),
        )

    def _auth(self, key=None):
        token = self.staff_token.key if key is None else key
        return {'HTTP_AUTHORIZATION': f'Token {token}'}

    def test_get_includes_upload_state_and_omits_zoom_url(self):
        Event.objects.filter(pk=self.event.pk).update(
            recording_s3_url='https://s3.example.test/recordings/api.mp4',
        )
        response = self.client.get(
            f'/api/events/{self.event.slug}',
            **self._auth(),
        )
        body = response.json()
        self.assertEqual(
            body['recording_s3_url'],
            'https://s3.example.test/recordings/api.mp4',
        )
        self.assertIsNotNone(body['recording_upload_enqueued_at'])
        self.assertEqual(body['recording_upload_status'], 'uploaded')
        self.assertNotIn('recording_zoom_download_url', body)

        by_id = self.client.get(
            f'/api/events/id/{self.event.pk}',
            **self._auth(),
        )
        self.assertEqual(by_id.json()['recording_upload_status'], 'uploaded')
        self.assertNotIn('recording_zoom_download_url', by_id.json())

    def test_retry_without_token_is_401(self):
        response = self.client.post(
            f'/api/events/{self.event.slug}/retry-recording-upload',
        )
        self.assertEqual(response.status_code, 401)

    def test_retry_unknown_slug_is_404(self):
        response = self.client.post(
            '/api/events/missing-recording/retry-recording-upload',
            **self._auth(),
        )
        self.assertEqual(response.status_code, 404)

    @patch('jobs.tasks.helpers.q_async_task')
    def test_retry_queues_stuck_github_event(self, mock_q_async):
        mock_q_async.return_value = 'api-retry-task'
        response = self.client.post(
            f'/api/events/{self.event.slug}/retry-recording-upload',
            **self._auth(),
        )
        self.assertEqual(
            response.json(),
            {'recording_upload_status': 'in_progress'},
        )
        self.assertEqual(mock_q_async.call_count, 1)
        self.assertEqual(
            mock_q_async.call_args[0][0],
            'jobs.tasks.recording_upload.upload_recording_to_s3',
        )

    @patch('jobs.tasks.helpers.q_async_task')
    def test_retry_in_progress_is_409(self, mock_q_async):
        Event.objects.filter(pk=self.event.pk).update(
            recording_upload_enqueued_at=timezone.now(),
        )
        response = self.client.post(
            f'/api/events/{self.event.slug}/retry-recording-upload',
            **self._auth(),
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['code'], 'recording_upload_in_progress')
        mock_q_async.assert_not_called()

    @patch('jobs.tasks.helpers.q_async_task')
    def test_retry_uploaded_is_409(self, mock_q_async):
        Event.objects.filter(pk=self.event.pk).update(
            recording_s3_url='https://s3.example.test/recordings/api.mp4',
        )
        response = self.client.post(
            f'/api/events/{self.event.slug}/retry-recording-upload',
            **self._auth(),
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['code'], 'recording_already_uploaded')
        mock_q_async.assert_not_called()

    @patch('jobs.tasks.helpers.q_async_task')
    def test_retry_without_download_url_is_422(self, mock_q_async):
        Event.objects.filter(pk=self.event.pk).update(
            recording_zoom_download_url='',
            recording_upload_enqueued_at=None,
        )
        response = self.client.post(
            f'/api/events/{self.event.slug}/retry-recording-upload',
            **self._auth(),
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()['code'], 'recording_download_url_missing')
        mock_q_async.assert_not_called()
