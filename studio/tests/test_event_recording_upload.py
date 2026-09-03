"""Studio retry UI for stuck Zoom recording uploads (issue #1505)."""

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from events.models import Event

User = get_user_model()


def _past_start_end():
    now = timezone.now()
    return now - timedelta(hours=3), now - timedelta(hours=1)


class StudioRecordingUploadCardTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='admin@test.com', password='pass', is_staff=True,
        )
        cls.member = User.objects.create_user(
            email='member-recording@test.com', password='pass',
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email='admin@test.com', password='pass')

    def _event(self, **overrides):
        start, end = _past_start_end()
        values = {
            'title': 'Recording Event',
            'slug': 'recording-event',
            'start_datetime': start,
            'end_datetime': end,
            'status': 'completed',
            'platform': 'zoom',
        }
        values.update(overrides)
        return Event.objects.create(**values)

    def test_stuck_card_shows_retry_button(self):
        event = self._event(
            recording_zoom_download_url='https://zoom.us/rec/download/stuck',
            recording_upload_enqueued_at=timezone.now() - timedelta(minutes=21),
        )
        response = self.client.get(f'/studio/events/{event.pk}/edit')
        self.assertContains(response, 'Recording upload')
        self.assertContains(
            response,
            'Upload stuck — retry to download from Zoom again.',
        )
        self.assertContains(response, 'Retry recording upload')
        self.assertContains(response, 'data-testid="retry-recording-upload-button"')
        self.assertNotContains(
            response,
            'data-testid="retry-recording-upload-button-disabled"',
        )
        self.assertNotContains(response, event.recording_zoom_download_url)

    def test_in_progress_card_disables_retry(self):
        event = self._event(
            slug='in-progress-recording',
            recording_zoom_download_url='https://zoom.us/rec/download/running',
            recording_upload_enqueued_at=timezone.now() - timedelta(minutes=2),
        )
        response = self.client.get(f'/studio/events/{event.pk}/edit')
        self.assertContains(response, 'Upload in progress.')
        self.assertContains(response, 'Upload already running')
        self.assertContains(
            response,
            'data-testid="retry-recording-upload-button-disabled"',
        )

    def test_uploaded_card_disables_retry(self):
        event = self._event(
            slug='uploaded-recording',
            recording_s3_url='https://s3.example.test/recordings/done.mp4',
        )
        response = self.client.get(f'/studio/events/{event.pk}/edit')
        self.assertContains(response, 'Recording is on S3.')
        self.assertContains(response, 'Already uploaded to S3')
        self.assertContains(
            response,
            'data-testid="retry-recording-upload-button-disabled"',
        )

    def test_idle_card_disables_retry(self):
        event = self._event(slug='idle-recording')
        response = self.client.get(f'/studio/events/{event.pk}/edit')
        self.assertContains(response, 'No Zoom download URL yet.')
        self.assertContains(
            response,
            'data-testid="retry-recording-upload-button-disabled"',
        )

    @patch('jobs.tasks.helpers.q_async_task')
    def test_retry_queues_stuck_upload(self, mock_q_async):
        mock_q_async.return_value = 'retry-task'
        event = self._event(
            slug='retry-stuck',
            recording_zoom_download_url='https://zoom.us/rec/download/stuck',
            recording_upload_enqueued_at=timezone.now() - timedelta(minutes=21),
        )
        response = self.client.post(
            reverse(
                'studio_event_retry_recording_upload',
                kwargs={'event_id': event.pk},
            ),
            follow=True,
        )
        self.assertContains(response, 'Recording upload queued.')
        self.assertContains(response, 'Upload in progress.')
        self.assertContains(
            response,
            'data-testid="retry-recording-upload-button-disabled"',
        )
        self.assertEqual(mock_q_async.call_count, 1)
        self.assertEqual(
            mock_q_async.call_args[0][1],
            event.id,
        )
        event.refresh_from_db()
        self.assertGreater(
            event.recording_upload_enqueued_at,
            timezone.now() - timedelta(minutes=1),
        )

    @patch('jobs.tasks.helpers.q_async_task')
    def test_retry_does_not_double_start_in_progress(self, mock_q_async):
        event = self._event(
            slug='retry-running',
            recording_zoom_download_url='https://zoom.us/rec/download/running',
            recording_upload_enqueued_at=timezone.now() - timedelta(minutes=2),
        )
        response = self.client.post(
            reverse(
                'studio_event_retry_recording_upload',
                kwargs={'event_id': event.pk},
            ),
            follow=True,
        )
        self.assertContains(response, 'Recording upload is already in progress.')
        mock_q_async.assert_not_called()

    @patch('jobs.tasks.helpers.q_async_task')
    def test_retry_uploaded_does_not_enqueue(self, mock_q_async):
        event = self._event(
            slug='retry-uploaded',
            recording_s3_url='https://s3.example.test/recordings/done.mp4',
            recording_zoom_download_url='https://zoom.us/rec/download/done',
        )
        response = self.client.post(
            reverse(
                'studio_event_retry_recording_upload',
                kwargs={'event_id': event.pk},
            ),
            follow=True,
        )
        self.assertContains(response, 'Recording is already on S3.')
        mock_q_async.assert_not_called()

    def test_anonymous_post_redirects_to_login(self):
        event = self._event(slug='anon-retry')
        client = Client()
        response = client.post(
            reverse(
                'studio_event_retry_recording_upload',
                kwargs={'event_id': event.pk},
            ),
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])

    def test_non_staff_post_is_forbidden(self):
        event = self._event(slug='member-retry')
        client = Client()
        client.login(email='member-recording@test.com', password='pass')
        response = client.post(
            reverse(
                'studio_event_retry_recording_upload',
                kwargs={'event_id': event.pk},
            ),
        )
        self.assertEqual(response.status_code, 403)
