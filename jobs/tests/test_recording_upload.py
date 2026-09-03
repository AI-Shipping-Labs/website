"""Tests for Zoom recording download and S3 upload - issue #110.

Covers:
- Background task: download from Zoom, upload to S3, store S3 URL
- S3 key structure: recordings/{year}/{event-slug}.mp4
- Error handling: missing recording, missing bucket config, download/upload failures
- Webhook integration: recording.completed triggers background job
- Recording model: s3_url field, video_url property priority
"""

import hashlib
import hmac
import json
import os
import tempfile
import time
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from email_app.models import EmailLog
from events.models import Event
from events.models.registration import EventRegistration
from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting

ZOOM_TEST_SECRET = 'test-zoom-webhook-secret'
ZOOM_TEST_CLIENT_ID = 'test-client-id'
ZOOM_TEST_CLIENT_SECRET = 'test-client-secret'
ZOOM_TEST_ACCOUNT_ID = 'test-account-id'


def make_zoom_signature(body, timestamp, secret=ZOOM_TEST_SECRET):
    """Create a valid Zoom webhook signature for testing."""
    message = f'v0:{timestamp}:{body}'
    sig = hmac.new(
        secret.encode('utf-8'),
        message.encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()
    return f'v0={sig}'


# --- Recording Model s3_url Field Tests ---


class RecordingS3UrlFieldTest(TestCase):
    """Test the s3_url field on the Recording model."""

    def test_s3_url_field_exists(self):
        """Recording model has an s3_url field."""
        recording = Event.objects.create(
            title='S3 Test',
            slug='s3-test',
            start_datetime=timezone.now(), status='completed',
        )
        self.assertEqual(recording.recording_s3_url, '')

    def test_zoom_download_url_field_defaults_blank(self):
        recording = Event.objects.create(
            title='Zoom URL Test',
            slug='zoom-url-test',
            start_datetime=timezone.now(),
            status='completed',
        )
        field = Event._meta.get_field('recording_zoom_download_url')
        self.assertEqual(field.max_length, 1000)
        self.assertEqual(recording.recording_zoom_download_url, '')

    def test_s3_url_stored(self):
        """s3_url can be set and retrieved."""
        recording = Event.objects.create(
            title='S3 Test',
            slug='s3-test-stored',
            start_datetime=timezone.now(), status='completed',
            recording_s3_url='https://bucket.s3.eu-central-1.amazonaws.com/recordings/2026/test.mp4',
        )
        recording.refresh_from_db()
        self.assertEqual(
            recording.recording_s3_url,
            'https://bucket.s3.eu-central-1.amazonaws.com/recordings/2026/test.mp4',
        )

    def test_video_url_prefers_s3_url(self):
        """video_url property returns s3_url when available."""
        recording = Event.objects.create(
            title='Priority Test',
            slug='priority-test',
            start_datetime=timezone.now(), status='completed',
            recording_url='https://zoom.us/rec/play/abc',
            recording_s3_url='https://bucket.s3.eu-central-1.amazonaws.com/recordings/2026/test.mp4',
        )
        self.assertEqual(
            recording.video_url,
            'https://bucket.s3.eu-central-1.amazonaws.com/recordings/2026/test.mp4',
        )

    def test_video_url_falls_back_to_youtube_url(self):
        """video_url property returns youtube_url when s3_url is empty."""
        recording = Event.objects.create(
            title='Fallback Test',
            slug='fallback-test',
            start_datetime=timezone.now(), status='completed',
            recording_url='https://zoom.us/rec/play/abc',
            recording_s3_url='',
        )
        self.assertEqual(recording.video_url, 'https://zoom.us/rec/play/abc')

    def test_video_url_falls_back_to_google_embed(self):
        """video_url falls back to google_embed_url when s3_url and youtube_url are empty."""
        recording = Event.objects.create(
            title='Google Fallback Test',
            slug='google-fallback-test',
            start_datetime=timezone.now(), status='completed',
            recording_embed_url='https://slides.google.com/embed/test',
            recording_s3_url='',
            recording_url='',
        )
        self.assertEqual(recording.video_url, 'https://slides.google.com/embed/test')


# --- Upload Recording to S3 Task Tests ---


@override_settings(
    AWS_S3_RECORDINGS_BUCKET='test-recordings-bucket',
    AWS_S3_RECORDINGS_REGION='eu-central-1',
    AWS_ACCESS_KEY_ID='test-key-id',
    AWS_SECRET_ACCESS_KEY='test-secret-key',
    ZOOM_CLIENT_ID=ZOOM_TEST_CLIENT_ID,
    ZOOM_CLIENT_SECRET=ZOOM_TEST_CLIENT_SECRET,
    ZOOM_ACCOUNT_ID=ZOOM_TEST_ACCOUNT_ID,
)
class UploadRecordingToS3Test(TestCase):
    """Test the upload_recording_to_s3 background task."""

    def setUp(self):
        from integrations.services import zoom
        zoom.clear_token_cache()
        clear_config_cache()

        self.event = Event.objects.create(
            title='Test Workshop',
            slug='test-workshop',
            start_datetime=timezone.now() - timedelta(hours=2),
            end_datetime=timezone.now() - timedelta(hours=1),
            timezone='Europe/Berlin',
            zoom_meeting_id='12345678901',
            status='completed',
            recording_url='https://zoom.us/rec/play/abc123',
            required_level=0,
            published=False,
        )
        self.recording = self.event

    def tearDown(self):
        clear_config_cache()

    @patch('jobs.tasks.recordings_s3.boto3.client')
    @patch('jobs.tasks.recording_upload.requests.get')
    @patch('integrations.services.zoom.requests.post')
    def test_successful_upload(self, mock_zoom_post, mock_requests_get, mock_boto_client):
        """Recording is downloaded from Zoom and uploaded to S3."""
        from jobs.tasks.recording_upload import upload_recording_to_s3

        # Mock Zoom token
        token_response = MagicMock()
        token_response.status_code = 200
        token_response.json.return_value = {
            'access_token': 'test-token',
            'expires_in': 3600,
        }
        mock_zoom_post.return_value = token_response

        # Mock Zoom download
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.iter_content.return_value = [b'fake-video-data']
        mock_response.raise_for_status = MagicMock()
        mock_requests_get.return_value = mock_response

        # Mock S3 client
        mock_s3 = MagicMock()
        mock_boto_client.return_value = mock_s3

        download_url = 'https://zoom.us/rec/download/abc123'
        result = upload_recording_to_s3(self.recording.id, download_url)

        self.assertEqual(result['status'], 'ok')
        self.assertIn('s3_url', result)

        # Verify S3 upload was called with a filesystem path, not BytesIO
        self.assertEqual(mock_s3.upload_file.call_count, 1)
        mock_s3.upload_fileobj.assert_not_called()
        upload_call = mock_s3.upload_file.call_args
        self.assertIsInstance(upload_call[0][0], str)
        # Check the bucket
        self.assertEqual(upload_call[0][1], 'test-recordings-bucket')
        # Check the key format: recordings/{year}/{slug}.mp4
        year = self.recording.start_datetime.date().year
        expected_key = f'recordings/{year}/test-workshop.mp4'
        self.assertEqual(upload_call[0][2], expected_key)
        # Check content type
        self.assertEqual(
            upload_call[1]['ExtraArgs']['ContentType'], 'video/mp4',
        )

        # Verify s3_url stored on recording
        self.recording.refresh_from_db()
        expected_url = f'https://test-recordings-bucket.s3.eu-central-1.amazonaws.com/recordings/{year}/test-workshop.mp4'
        self.assertEqual(self.recording.recording_s3_url, expected_url)

    @patch('jobs.tasks.recordings_s3.boto3.client')
    @patch('jobs.tasks.recording_upload.requests.get')
    @patch('integrations.services.zoom.requests.post')
    def test_successful_upload_notifies_host_after_s3_save(
        self,
        mock_zoom_post,
        mock_requests_get,
        mock_boto_client,
    ):
        """Host notification fires only after recording_s3_url is saved."""
        from jobs.tasks.recording_upload import upload_recording_to_s3

        self.event.host_email = 'host@example.com'
        self.event.save(update_fields=['host_email'])
        attendee = get_user_model().objects.create_user(
            email='attendee@example.com',
            password='pw',
        )
        EventRegistration.objects.create(event=self.event, user=attendee)

        token_response = MagicMock()
        token_response.status_code = 200
        token_response.json.return_value = {
            'access_token': 'test-token',
            'expires_in': 3600,
        }
        mock_zoom_post.return_value = token_response

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.iter_content.return_value = [b'fake-video-data']
        mock_response.raise_for_status = MagicMock()
        mock_requests_get.return_value = mock_response

        mock_s3 = MagicMock()
        mock_boto_client.return_value = mock_s3

        result = upload_recording_to_s3(
            self.recording.id,
            'https://zoom.us/rec/download/abc123',
        )

        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['host_notification_status'], 'sent')
        self.assertEqual(result['host_notification_recipient_count'], 1)
        self.assertEqual(result['host_notification_skipped_reason'], '')
        self.assertEqual(len(result['host_notification_email_log_ids']), 1)
        self.assertEqual(
            result['host_notification_results'][0]['email'],
            'host@example.com',
        )

        self.event.refresh_from_db()
        self.assertTrue(self.event.recording_s3_url)
        # Issue #1134 (Phase B): auto-publish defaults ON, so a successful
        # upload flips the event live (and stamps published_at) before the
        # host notification fires.
        self.assertTrue(self.event.published)
        self.assertIsNotNone(self.event.published_at)

        self.assertEqual(
            EmailLog.objects.filter(
                event=self.event,
                email_type='event_recording_ready',
                recipient_email='host@example.com',
            ).count(),
            1,
        )
        self.assertFalse(
            EmailLog.objects.filter(
                event=self.event,
                email_type='post_event_followup',
            ).exists()
        )

    def _run_upload_with_mocks(self, mock_zoom_post, mock_requests_get,
                               mock_boto_client):
        """Wire the standard Zoom/download/S3 mocks and run the upload task."""
        from jobs.tasks.recording_upload import upload_recording_to_s3

        token_response = MagicMock()
        token_response.status_code = 200
        token_response.json.return_value = {
            'access_token': 'test-token',
            'expires_in': 3600,
        }
        mock_zoom_post.return_value = token_response

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.iter_content.return_value = [b'fake-video-data']
        mock_response.raise_for_status = MagicMock()
        mock_requests_get.return_value = mock_response

        mock_s3 = MagicMock()
        mock_boto_client.return_value = mock_s3

        return upload_recording_to_s3(
            self.recording.id,
            'https://zoom.us/rec/download/abc123',
        )

    @patch('jobs.tasks.recordings_s3.boto3.client')
    @patch('jobs.tasks.recording_upload.requests.get')
    @patch('integrations.services.zoom.requests.post')
    def test_auto_publish_enabled_by_default_publishes_event_on_upload(
        self,
        mock_zoom_post,
        mock_requests_get,
        mock_boto_client,
    ):
        """Issue #1134: with the flag unset (default ON), a successful upload
        publishes the previously-unpublished event and stamps published_at."""
        self.assertFalse(self.event.published)

        with patch(
            'events.services.recording_ready_notification.notify_recording_ready',
        ) as mock_notify:
            mock_notify.return_value = {
                'status': 'sent', 'recipient_count': 1,
                'attempted_recipient_count': 1, 'skipped_reason': '',
                'email_log_ids': [], 'results': [],
            }
            result = self._run_upload_with_mocks(
                mock_zoom_post, mock_requests_get, mock_boto_client,
            )

        self.assertEqual(result['status'], 'ok')
        mock_notify.assert_called_once()
        self.event.refresh_from_db()
        self.assertTrue(self.event.published)
        self.assertIsNotNone(self.event.published_at)

    @patch('jobs.tasks.recordings_s3.boto3.client')
    @patch('jobs.tasks.recording_upload.requests.get')
    @patch('integrations.services.zoom.requests.post')
    def test_auto_publish_disabled_preserves_review_flow(
        self,
        mock_zoom_post,
        mock_requests_get,
        mock_boto_client,
    ):
        """Issue #1134: with the flag explicitly off, a successful upload
        leaves the event unpublished so the review-first flow is preserved."""
        IntegrationSetting.objects.update_or_create(
            key='RECORDING_AUTO_PUBLISH_ON_S3_UPLOAD',
            defaults={
                'value': 'false',
                'is_secret': False,
                'group': 's3_recordings',
            },
        )
        clear_config_cache()
        self.addCleanup(clear_config_cache)
        self.assertFalse(self.event.published)

        with patch(
            'events.services.recording_ready_notification.notify_recording_ready',
        ) as mock_notify:
            mock_notify.return_value = {
                'status': 'sent', 'recipient_count': 1,
                'attempted_recipient_count': 1, 'skipped_reason': '',
                'email_log_ids': [], 'results': [],
            }
            result = self._run_upload_with_mocks(
                mock_zoom_post, mock_requests_get, mock_boto_client,
            )

        self.assertEqual(result['status'], 'ok')
        self.event.refresh_from_db()
        self.assertFalse(self.event.published)
        self.assertIsNone(self.event.published_at)

    @patch('jobs.tasks.recordings_s3.boto3.client')
    @patch('jobs.tasks.recording_upload.requests.get')
    @patch('integrations.services.zoom.requests.post')
    @patch('events.services.recording_ready_notification.notify_recording_ready')
    def test_notification_failure_does_not_retry_successful_upload(
        self,
        mock_notify,
        mock_zoom_post,
        mock_requests_get,
        mock_boto_client,
    ):
        """Notification errors are surfaced without rolling back S3 state."""
        from jobs.tasks.recording_upload import upload_recording_to_s3

        mock_notify.side_effect = RuntimeError('email path exploded')

        token_response = MagicMock()
        token_response.status_code = 200
        token_response.json.return_value = {
            'access_token': 'test-token',
            'expires_in': 3600,
        }
        mock_zoom_post.return_value = token_response

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.iter_content.return_value = [b'fake-video-data']
        mock_response.raise_for_status = MagicMock()
        mock_requests_get.return_value = mock_response

        mock_s3 = MagicMock()
        mock_boto_client.return_value = mock_s3

        result = upload_recording_to_s3(
            self.recording.id,
            'https://zoom.us/rec/download/abc123',
        )

        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['host_notification_status'], 'error')
        self.assertEqual(
            result['host_notification_skipped_reason'],
            'notification_error',
        )
        self.assertEqual(result['host_notification_email_log_ids'], [])
        self.assertEqual(
            result['host_notification_results'][0]['reason'],
            'RuntimeError',
        )
        self.recording.refresh_from_db()
        self.assertTrue(self.recording.recording_s3_url)

    @patch('jobs.tasks.recordings_s3.boto3.client')
    @patch('jobs.tasks.recording_upload.requests.get')
    @patch('integrations.services.zoom.requests.post')
    def test_s3_key_structure(self, mock_zoom_post, mock_requests_get, mock_boto_client):
        """S3 key follows recordings/{year}/{slug}.mp4 pattern."""
        from jobs.tasks.recording_upload import upload_recording_to_s3

        # Mock Zoom token
        token_response = MagicMock()
        token_response.status_code = 200
        token_response.json.return_value = {
            'access_token': 'test-token',
            'expires_in': 3600,
        }
        mock_zoom_post.return_value = token_response

        # Mock download
        mock_response = MagicMock()
        mock_response.iter_content.return_value = [b'data']
        mock_response.raise_for_status = MagicMock()
        mock_requests_get.return_value = mock_response

        # Mock S3
        mock_s3 = MagicMock()
        mock_boto_client.return_value = mock_s3

        upload_recording_to_s3(
            self.recording.id,
            'https://zoom.us/rec/download/abc',
        )

        # Verify key structure
        upload_call = mock_s3.upload_file.call_args
        key = upload_call[0][2]
        year = self.recording.start_datetime.date().year
        self.assertEqual(key, f'recordings/{year}/test-workshop.mp4')

    def test_missing_recording(self):
        """Task handles missing recording gracefully."""
        from jobs.tasks.recording_upload import upload_recording_to_s3

        result = upload_recording_to_s3(99999, 'https://example.com/video.mp4')
        self.assertEqual(result['status'], 'error')
        self.assertIn('not found', result['message'])

    @override_settings(AWS_S3_RECORDINGS_BUCKET='')
    def test_missing_bucket_config(self):
        """Task handles missing S3 bucket configuration."""
        from jobs.tasks.recording_upload import upload_recording_to_s3

        result = upload_recording_to_s3(
            self.recording.id,
            'https://example.com/video.mp4',
        )
        self.assertEqual(result['status'], 'error')
        self.assertIn('not configured', result['message'])

    @patch('jobs.tasks.recording_upload.requests.get')
    @patch('integrations.services.zoom.requests.post')
    def test_download_failure_raises(self, mock_zoom_post, mock_requests_get):
        """Task raises on download failure to trigger django-q2 retry."""
        import requests as req

        from jobs.tasks.recording_upload import upload_recording_to_s3

        # Mock Zoom token
        token_response = MagicMock()
        token_response.status_code = 200
        token_response.json.return_value = {
            'access_token': 'test-token',
            'expires_in': 3600,
        }
        mock_zoom_post.return_value = token_response

        # Mock failed download
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = req.HTTPError('Download failed')
        mock_requests_get.return_value = mock_response

        with self.assertRaises(req.HTTPError):
            upload_recording_to_s3(
                self.recording.id,
                'https://zoom.us/rec/download/fail',
            )
        self.recording.refresh_from_db()
        self.assertIsNotNone(self.recording.recording_upload_enqueued_at)

    @patch('jobs.tasks.recordings_s3.boto3.client')
    @patch('jobs.tasks.recording_upload.requests.get')
    @patch('integrations.services.zoom.requests.post')
    def test_s3_upload_failure_raises(self, mock_zoom_post, mock_requests_get, mock_boto_client):
        """Task raises on S3 upload failure to trigger django-q2 retry."""
        from botocore.exceptions import ClientError

        from jobs.tasks.recording_upload import upload_recording_to_s3

        # Mock Zoom token
        token_response = MagicMock()
        token_response.status_code = 200
        token_response.json.return_value = {
            'access_token': 'test-token',
            'expires_in': 3600,
        }
        mock_zoom_post.return_value = token_response

        # Mock successful download
        mock_response = MagicMock()
        mock_response.iter_content.return_value = [b'data']
        mock_response.raise_for_status = MagicMock()
        mock_requests_get.return_value = mock_response

        # Mock S3 failure
        mock_s3 = MagicMock()
        mock_s3.upload_file.side_effect = ClientError(
            {'Error': {'Code': 'AccessDenied', 'Message': 'Forbidden'}},
            'PutObject',
        )
        mock_boto_client.return_value = mock_s3

        with self.assertRaises(ClientError):
            upload_recording_to_s3(
                self.recording.id,
                'https://zoom.us/rec/download/abc',
            )

        # s3_url should NOT be set on the recording
        self.recording.refresh_from_db()
        self.assertEqual(self.recording.recording_s3_url, '')
        self.assertFalse(
            EmailLog.objects.filter(email_type='event_recording_ready').exists()
        )

    @patch('jobs.tasks.recordings_s3.boto3.client')
    @patch('jobs.tasks.recording_upload.requests.get')
    @patch('integrations.services.zoom.requests.post')
    def test_download_url_gets_access_token(self, mock_zoom_post, mock_requests_get, mock_boto_client):
        """Download URL is authenticated with Zoom access token."""
        from jobs.tasks.recording_upload import upload_recording_to_s3

        # Mock Zoom token
        token_response = MagicMock()
        token_response.status_code = 200
        token_response.json.return_value = {
            'access_token': 'my-zoom-token-xyz',
            'expires_in': 3600,
        }
        mock_zoom_post.return_value = token_response

        # Mock download
        mock_response = MagicMock()
        mock_response.iter_content.return_value = [b'data']
        mock_response.raise_for_status = MagicMock()
        mock_requests_get.return_value = mock_response

        # Mock S3
        mock_s3 = MagicMock()
        mock_boto_client.return_value = mock_s3

        download_url = 'https://zoom.us/rec/download/abc123'
        upload_recording_to_s3(self.recording.id, download_url)

        # Verify the download URL includes access_token
        actual_url = mock_requests_get.call_args[0][0]
        self.assertIn('access_token=my-zoom-token-xyz', actual_url)
        self.assertTrue(actual_url.startswith(download_url))

    @patch('jobs.tasks.recordings_s3.boto3.client')
    @patch('jobs.tasks.recording_upload.requests.get')
    @patch('integrations.services.zoom.requests.post')
    def test_download_url_with_existing_query_params(self, mock_zoom_post, mock_requests_get, mock_boto_client):
        """Download URL with existing query params uses & separator."""
        from jobs.tasks.recording_upload import upload_recording_to_s3

        # Mock Zoom token
        token_response = MagicMock()
        token_response.status_code = 200
        token_response.json.return_value = {
            'access_token': 'token-123',
            'expires_in': 3600,
        }
        mock_zoom_post.return_value = token_response

        # Mock download
        mock_response = MagicMock()
        mock_response.iter_content.return_value = [b'data']
        mock_response.raise_for_status = MagicMock()
        mock_requests_get.return_value = mock_response

        # Mock S3
        mock_s3 = MagicMock()
        mock_boto_client.return_value = mock_s3

        download_url = 'https://zoom.us/rec/download/abc?filetype=mp4'
        upload_recording_to_s3(self.recording.id, download_url)

        actual_url = mock_requests_get.call_args[0][0]
        self.assertIn('&access_token=token-123', actual_url)

    @patch('jobs.tasks.recordings_s3.boto3.client')
    @patch('jobs.tasks.recording_upload.requests.get')
    @patch('integrations.services.zoom.requests.post')
    def test_uses_studio_config_for_recordings_s3(
        self,
        mock_zoom_post,
        mock_requests_get,
        mock_boto_client,
    ):
        """Studio IntegrationSetting values override process settings."""
        from jobs.tasks.recording_upload import upload_recording_to_s3

        IntegrationSetting.objects.bulk_create([
            IntegrationSetting(
                key='AWS_S3_RECORDINGS_BUCKET',
                value='studio-recordings-bucket',
                group='s3_recordings',
            ),
            IntegrationSetting(
                key='AWS_S3_RECORDINGS_REGION',
                value='us-west-2',
                group='s3_recordings',
            ),
            IntegrationSetting(
                key='AWS_ACCESS_KEY_ID',
                value='studio-access-key',
                is_secret=True,
                group='ses',
            ),
            IntegrationSetting(
                key='AWS_SECRET_ACCESS_KEY',
                value='studio-secret-key',
                is_secret=True,
                group='ses',
            ),
        ])
        clear_config_cache()

        token_response = MagicMock()
        token_response.status_code = 200
        token_response.json.return_value = {
            'access_token': 'test-token',
            'expires_in': 3600,
        }
        mock_zoom_post.return_value = token_response

        mock_response = MagicMock()
        mock_response.iter_content.return_value = [b'data']
        mock_response.raise_for_status = MagicMock()
        mock_requests_get.return_value = mock_response

        mock_s3 = MagicMock()
        mock_boto_client.return_value = mock_s3

        result = upload_recording_to_s3(
            self.recording.id,
            'https://zoom.us/rec/download/abc',
        )

        self.assertEqual(result['status'], 'ok')
        mock_boto_client.assert_called_once_with(
            's3',
            region_name='us-west-2',
            aws_access_key_id='studio-access-key',
            aws_secret_access_key='studio-secret-key',
        )
        upload_call = mock_s3.upload_file.call_args
        self.assertEqual(upload_call[0][1], 'studio-recordings-bucket')

        self.recording.refresh_from_db()
        self.assertIn('studio-recordings-bucket.s3.us-west-2', self.recording.recording_s3_url)


# --- Webhook Integration Tests ---


@override_settings(ZOOM_WEBHOOK_SECRET_TOKEN=ZOOM_TEST_SECRET)
class WebhookTriggersS3UploadJobTest(TestCase):
    """Test that recording.completed webhook enqueues the S3 upload background job."""

    def setUp(self):
        self.client = Client()
        self.event = Event.objects.create(
            title='Upload Workshop',
            slug='upload-workshop',
            description='Learn about uploads.',
            start_datetime=timezone.now() - timedelta(hours=3),
            end_datetime=timezone.now() - timedelta(hours=1),
            timezone='Europe/Berlin',
            zoom_meeting_id='55555555555',
            zoom_join_url='https://zoom.us/j/55555555555',
            tags=['uploads'],
            required_level=0,
            status='upcoming',
        )

    def _post_webhook(self, payload_dict):
        """Helper to post a webhook with valid signature."""
        body = json.dumps(payload_dict)
        timestamp = str(int(time.time()))
        signature = make_zoom_signature(body, timestamp)
        return self.client.post(
            '/api/webhooks/zoom',
            data=body,
            content_type='application/json',
            HTTP_X_ZM_REQUEST_TIMESTAMP=timestamp,
            HTTP_X_ZM_SIGNATURE=signature,
        )

    @patch('integrations.views.zoom_webhook.async_task', create=True)
    @patch('jobs.tasks.helpers.q_async_task')
    def test_recording_completed_enqueues_upload_job(self, mock_q_async, mock_async):
        """recording.completed webhook enqueues S3 upload background job."""
        # We patch at the django-q2 level to capture enqueue calls
        mock_q_async.return_value = 'task-id-123'

        payload = {
            'event': 'recording.completed',
            'payload': {
                'object': {
                    'id': '55555555555',
                    'topic': 'Upload Workshop',
                    'share_url': 'https://zoom.us/rec/share/test',
                    'recording_files': [
                        {
                            'recording_type': 'shared_screen_with_speaker_view',
                            'play_url': 'https://zoom.us/rec/play/test',
                            'download_url': 'https://zoom.us/rec/download/test',
                        },
                    ],
                },
            },
        }
        response = self._post_webhook(payload)
        self.assertEqual(response.status_code, 200)

        # Recording should be created
        recording = Event.objects.filter(slug='upload-workshop').first()
        self.assertIsNotNone(recording)

        # Background job should have been enqueued
        mock_q_async.assert_called_once()
        call_args = mock_q_async.call_args
        # First arg is the function path
        self.assertEqual(
            call_args[0][0],
            'jobs.tasks.recording_upload.upload_recording_to_s3',
        )
        # Second arg is the recording ID
        self.assertEqual(call_args[0][1], recording.id)
        # Third arg is the download URL
        self.assertEqual(
            call_args[0][2],
            'https://zoom.us/rec/download/test',
        )
        q_options = call_args[1]['q_options']
        self.assertEqual(q_options['timeout'], 900)
        self.assertEqual(q_options['retry'], 960)
        self.assertGreater(q_options['retry'], q_options['timeout'])
        self.assertEqual(q_options['max_attempts'], 4)
        recording.refresh_from_db()
        self.assertEqual(
            recording.recording_zoom_download_url,
            'https://zoom.us/rec/download/test',
        )

    def test_no_download_url_skips_upload_job(self):
        """If no download URL, S3 upload job is not enqueued."""
        payload = {
            'event': 'recording.completed',
            'payload': {
                'object': {
                    'id': '55555555555',
                    'share_url': 'https://zoom.us/rec/share/test',
                    'recording_files': [
                        {
                            'recording_type': 'chat_file',
                            'play_url': 'https://zoom.us/rec/play/chat',
                        },
                    ],
                },
            },
        }

        with patch('jobs.tasks.helpers.q_async_task') as mock_q_async:
            response = self._post_webhook(payload)

        self.assertEqual(response.status_code, 200)

        # Recording should still be created (with share_url as fallback)
        recording = Event.objects.filter(slug='upload-workshop').first()
        self.assertIsNotNone(recording)
        self.assertEqual(recording.recording_url, 'https://zoom.us/rec/share/test')

        # But no S3 upload job should be enqueued
        mock_q_async.assert_not_called()

    @patch('jobs.tasks.helpers.q_async_task')
    def test_recording_completed_extracts_download_url(self, mock_q_async):
        """Webhook extracts download_url from the preferred recording file."""
        mock_q_async.return_value = 'task-id-456'

        payload = {
            'event': 'recording.completed',
            'payload': {
                'object': {
                    'id': '55555555555',
                    'recording_files': [
                        {
                            'recording_type': 'audio_only',
                            'play_url': 'https://zoom.us/rec/play/audio',
                            'download_url': 'https://zoom.us/rec/download/audio',
                        },
                        {
                            'recording_type': 'shared_screen_with_speaker_view',
                            'play_url': 'https://zoom.us/rec/play/video',
                            'download_url': 'https://zoom.us/rec/download/video',
                        },
                    ],
                },
            },
        }
        response = self._post_webhook(payload)
        self.assertEqual(response.status_code, 200)

        # Should use the preferred recording type's download URL
        call_args = mock_q_async.call_args
        self.assertEqual(
            call_args[0][2],
            'https://zoom.us/rec/download/video',
        )


# --- Settings Tests ---


class S3RecordingsSettingsTest(TestCase):
    """Test S3 recording settings are properly configured."""

    @override_settings(
        AWS_S3_RECORDINGS_BUCKET='my-bucket',
        AWS_S3_RECORDINGS_REGION='us-west-2',
    )
    def test_s3_settings_available(self):
        from django.conf import settings
        self.assertEqual(settings.AWS_S3_RECORDINGS_BUCKET, 'my-bucket')
        self.assertEqual(settings.AWS_S3_RECORDINGS_REGION, 'us-west-2')

    def test_s3_settings_default_values(self):
        from django.conf import settings
        self.assertIsInstance(settings.AWS_S3_RECORDINGS_BUCKET, str)
        self.assertIsInstance(settings.AWS_S3_RECORDINGS_REGION, str)

    def test_global_q_cluster_timeout_stays_300(self):
        from django.conf import settings
        self.assertEqual(settings.Q_CLUSTER['timeout'], 300)
        self.assertLess(
            settings.Q_CLUSTER['timeout'],
            settings.Q_CLUSTER['retry'],
        )


class RecordingUploadStreamingTest(TestCase):
    """Issue #1505: stream Zoom bytes to disk without buffering the MP4."""

    def test_download_never_joins_chunks_and_stays_at_chunk_size(self):
        from jobs.tasks.recording_upload import (
            RECORDING_DOWNLOAD_CHUNK_SIZE,
            _download_from_zoom,
        )

        chunks = [
            b'a' * RECORDING_DOWNLOAD_CHUNK_SIZE,
            b'b' * RECORDING_DOWNLOAD_CHUNK_SIZE,
            b'c' * (RECORDING_DOWNLOAD_CHUNK_SIZE // 2),
        ]
        write_sizes = []
        mock_response = MagicMock()
        mock_response.iter_content.return_value = chunks
        mock_response.raise_for_status = MagicMock()

        original_open = open

        def tracking_open(path, mode='r', *args, **kwargs):
            handle = original_open(path, mode, *args, **kwargs)
            if 'w' not in mode:
                return handle
            original_write = handle.write

            def tracked_write(data):
                write_sizes.append(len(data))
                return original_write(data)

            handle.write = tracked_write
            return handle

        dest = tempfile.NamedTemporaryFile(delete=False, suffix='.mp4')
        dest.close()
        try:
            with patch(
                'jobs.tasks.recording_upload.requests.get',
                return_value=mock_response,
            ), patch('builtins.open', tracking_open):
                written = _download_from_zoom(
                    'https://zoom.us/rec/download/stream',
                    dest.name,
                )
        finally:
            os.unlink(dest.name)

        self.assertEqual(written, sum(len(chunk) for chunk in chunks))
        self.assertEqual(write_sizes, [len(chunk) for chunk in chunks])
        self.assertLessEqual(max(write_sizes), RECORDING_DOWNLOAD_CHUNK_SIZE)
        self.assertLess(max(write_sizes), sum(len(chunk) for chunk in chunks))
        mock_response.iter_content.assert_called_once_with(
            chunk_size=RECORDING_DOWNLOAD_CHUNK_SIZE,
        )
        self.assertEqual(mock_response.close.call_count, 1)

    def test_http_response_closed_when_raise_for_status_fails(self):
        import requests as req

        from jobs.tasks.recording_upload import _download_from_zoom

        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = req.HTTPError('nope')
        dest = tempfile.NamedTemporaryFile(delete=False, suffix='.mp4')
        dest.close()
        try:
            with patch(
                'jobs.tasks.recording_upload.requests.get',
                return_value=mock_response,
            ):
                with self.assertRaises(req.HTTPError):
                    _download_from_zoom(
                        'https://zoom.us/rec/download/fail',
                        dest.name,
                    )
        finally:
            os.unlink(dest.name)
        self.assertEqual(mock_response.close.call_count, 1)

    def test_http_response_closed_when_chunk_iteration_fails(self):
        from jobs.tasks.recording_upload import _download_from_zoom

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.iter_content.side_effect = RuntimeError('stream broke')
        dest = tempfile.NamedTemporaryFile(delete=False, suffix='.mp4')
        dest.close()
        try:
            with patch(
                'jobs.tasks.recording_upload.requests.get',
                return_value=mock_response,
            ):
                with self.assertRaises(RuntimeError):
                    _download_from_zoom(
                        'https://zoom.us/rec/download/fail',
                        dest.name,
                    )
        finally:
            if os.path.exists(dest.name):
                os.unlink(dest.name)
        self.assertEqual(mock_response.close.call_count, 1)

    def test_timeouts_are_aligned(self):
        from django.conf import settings

        from jobs.tasks.recording_upload import (
            RECORDING_UPLOAD_HTTP_TIMEOUT_SECONDS,
            RECORDING_UPLOAD_TASK_RETRY_SECONDS,
            RECORDING_UPLOAD_TASK_TIMEOUT_SECONDS,
        )

        self.assertEqual(RECORDING_UPLOAD_TASK_TIMEOUT_SECONDS, 900)
        self.assertEqual(RECORDING_UPLOAD_HTTP_TIMEOUT_SECONDS, 600)
        self.assertEqual(RECORDING_UPLOAD_TASK_RETRY_SECONDS, 960)
        self.assertLess(
            RECORDING_UPLOAD_HTTP_TIMEOUT_SECONDS,
            RECORDING_UPLOAD_TASK_TIMEOUT_SECONDS,
        )
        self.assertGreater(
            RECORDING_UPLOAD_TASK_RETRY_SECONDS,
            RECORDING_UPLOAD_TASK_TIMEOUT_SECONDS,
        )
        self.assertEqual(settings.Q_CLUSTER['timeout'], 300)


@override_settings(
    AWS_S3_RECORDINGS_BUCKET='test-recordings-bucket',
    AWS_S3_RECORDINGS_REGION='eu-central-1',
    AWS_ACCESS_KEY_ID='test-key-id',
    AWS_SECRET_ACCESS_KEY='test-secret-key',
    ZOOM_CLIENT_ID=ZOOM_TEST_CLIENT_ID,
    ZOOM_CLIENT_SECRET=ZOOM_TEST_CLIENT_SECRET,
    ZOOM_ACCOUNT_ID=ZOOM_TEST_ACCOUNT_ID,
)
class RecordingUploadLeaseAndTempfileTest(TestCase):
    def setUp(self):
        from integrations.services import zoom
        zoom.clear_token_cache()
        clear_config_cache()
        self.event = Event.objects.create(
            title='Lease Workshop',
            slug='lease-workshop',
            start_datetime=timezone.now() - timedelta(hours=2),
            end_datetime=timezone.now() - timedelta(hours=1),
            timezone='Europe/Berlin',
            zoom_meeting_id='22222222222',
            status='completed',
            recording_zoom_download_url='https://zoom.us/rec/download/lease',
        )

    def tearDown(self):
        clear_config_cache()

    def _token_response(self):
        token_response = MagicMock()
        token_response.status_code = 200
        token_response.json.return_value = {
            'access_token': 'test-token',
            'expires_in': 3600,
        }
        return token_response

    def _download_response(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.iter_content.return_value = [b'fake-video-data']
        mock_response.raise_for_status = MagicMock()
        return mock_response

    @patch('jobs.tasks.recordings_s3.boto3.client')
    @patch('jobs.tasks.recording_upload.requests.get')
    @patch('integrations.services.zoom.requests.post')
    def test_attempt_refreshes_lease_and_deletes_tempfile(
        self, mock_zoom_post, mock_requests_get, mock_boto_client,
    ):
        from jobs.tasks.recording_upload import upload_recording_to_s3

        mock_zoom_post.return_value = self._token_response()
        mock_requests_get.return_value = self._download_response()
        mock_s3 = MagicMock()
        captured = {}

        def capture_upload(path, bucket, key, ExtraArgs=None):
            captured['path'] = path
            captured['existed'] = os.path.exists(path)
            captured['payload'] = open(path, 'rb').read()

        mock_s3.upload_file.side_effect = capture_upload
        mock_boto_client.return_value = mock_s3
        stale = timezone.now() - timedelta(minutes=30)
        Event.objects.filter(pk=self.event.pk).update(
            recording_upload_enqueued_at=stale,
        )

        result = upload_recording_to_s3(
            self.event.id,
            'https://zoom.us/rec/download/lease',
        )

        self.assertEqual(result['status'], 'ok')
        self.event.refresh_from_db()
        self.assertGreater(self.event.recording_upload_enqueued_at, stale)
        self.assertTrue(self.event.recording_s3_url)
        self.assertTrue(captured['existed'])
        self.assertEqual(captured['payload'], b'fake-video-data')
        self.assertFalse(os.path.exists(captured['path']))
        self.assertNotIsInstance(captured['path'], bytes)

    @patch('jobs.tasks.recordings_s3.boto3.client')
    @patch('jobs.tasks.recording_upload.requests.get')
    @patch('integrations.services.zoom.requests.post')
    def test_tempfile_deleted_after_upload_failure(
        self, mock_zoom_post, mock_requests_get, mock_boto_client,
    ):
        from botocore.exceptions import ClientError

        from jobs.tasks.recording_upload import upload_recording_to_s3

        mock_zoom_post.return_value = self._token_response()
        mock_requests_get.return_value = self._download_response()
        mock_s3 = MagicMock()
        captured = {}

        def fail_upload(path, bucket, key, ExtraArgs=None):
            captured['path'] = path
            raise ClientError(
                {'Error': {'Code': 'AccessDenied', 'Message': 'Forbidden'}},
                'PutObject',
            )

        mock_s3.upload_file.side_effect = fail_upload
        mock_boto_client.return_value = mock_s3

        with self.assertRaises(ClientError):
            upload_recording_to_s3(
                self.event.id,
                'https://zoom.us/rec/download/lease',
            )

        self.assertFalse(os.path.exists(captured['path']))
        self.event.refresh_from_db()
        self.assertEqual(self.event.recording_s3_url, '')


class RetryStuckRecordingUploadsTest(TestCase):
    def setUp(self):
        now = timezone.now()
        self.stuck = Event.objects.create(
            title='Stuck Upload',
            slug='stuck-upload',
            start_datetime=now - timedelta(hours=3),
            end_datetime=now - timedelta(hours=1),
            recording_zoom_download_url='https://zoom.us/rec/download/stuck',
            recording_upload_enqueued_at=now - timedelta(minutes=21),
        )
        self.active = Event.objects.create(
            title='Active Upload',
            slug='active-upload',
            start_datetime=now - timedelta(hours=3),
            end_datetime=now - timedelta(hours=1),
            recording_zoom_download_url='https://zoom.us/rec/download/active',
            recording_upload_enqueued_at=now - timedelta(minutes=5),
        )
        self.done = Event.objects.create(
            title='Done Upload',
            slug='done-upload',
            start_datetime=now - timedelta(hours=3),
            end_datetime=now - timedelta(hours=1),
            recording_zoom_download_url='https://zoom.us/rec/download/done',
            recording_s3_url='https://s3.example.test/recordings/done.mp4',
            recording_upload_enqueued_at=now - timedelta(minutes=40),
        )

    @patch('jobs.tasks.helpers.q_async_task')
    def test_reclaim_enqueues_expired_leases_only(self, mock_q_async):
        from django.db.models.query import QuerySet

        from jobs.tasks.recording_upload import retry_stuck_recording_uploads

        mock_q_async.return_value = 'reclaim-task'
        with patch.object(
            QuerySet, 'select_for_update', autospec=True,
            wraps=QuerySet.select_for_update,
        ) as mock_sfu:
            result = retry_stuck_recording_uploads()

        self.assertEqual(result['queued'], 1)
        self.assertTrue(mock_sfu.called)
        self.assertEqual(mock_q_async.call_count, 1)
        self.assertEqual(
            mock_q_async.call_args[0][1],
            self.stuck.id,
        )
        self.stuck.refresh_from_db()
        self.assertGreater(
            self.stuck.recording_upload_enqueued_at,
            timezone.now() - timedelta(minutes=1),
        )
        self.active.refresh_from_db()
        self.assertLess(
            self.active.recording_upload_enqueued_at,
            timezone.now() - timedelta(minutes=4),
        )

    @patch('jobs.tasks.helpers.q_async_task')
    def test_reclaim_does_not_double_enqueue_after_claim(self, mock_q_async):
        from jobs.tasks.recording_upload import retry_stuck_recording_uploads

        mock_q_async.return_value = 'reclaim-task'
        first = retry_stuck_recording_uploads()
        second = retry_stuck_recording_uploads()
        self.assertEqual(first['queued'], 1)
        self.assertEqual(second['queued'], 0)
        self.assertEqual(mock_q_async.call_count, 1)

    @patch('jobs.tasks.helpers.q_async_task')
    def test_reclaim_caps_one_pass(self, mock_q_async):
        from jobs.tasks.recording_upload import retry_stuck_recording_uploads

        mock_q_async.return_value = 'reclaim-task'
        now = timezone.now()
        for index in range(3):
            Event.objects.create(
                title=f'Backlog {index}',
                slug=f'backlog-{index}',
                start_datetime=now - timedelta(hours=3),
                end_datetime=now - timedelta(hours=1),
                recording_zoom_download_url=(
                    f'https://zoom.us/rec/download/backlog-{index}'
                ),
                recording_upload_enqueued_at=now - timedelta(minutes=30),
            )
        result = retry_stuck_recording_uploads(limit=2)
        self.assertEqual(result['queued'], 2)
        self.assertEqual(result['examined'], 2)
        self.assertEqual(mock_q_async.call_count, 2)

