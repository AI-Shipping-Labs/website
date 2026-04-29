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
import time
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.test import Client, TestCase, override_settings
from django.utils import timezone

from events.models import Event
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
            event_type='live',
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

    @patch('jobs.tasks.recording_upload.boto3.client')
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

        # Verify S3 upload was called
        mock_s3.upload_fileobj.assert_called_once()
        upload_call = mock_s3.upload_fileobj.call_args
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

    @patch('jobs.tasks.recording_upload.boto3.client')
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
        upload_call = mock_s3.upload_fileobj.call_args
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

    @patch('jobs.tasks.recording_upload.boto3.client')
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
        mock_s3.upload_fileobj.side_effect = ClientError(
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

    @patch('jobs.tasks.recording_upload.boto3.client')
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

    @patch('jobs.tasks.recording_upload.boto3.client')
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

    @patch('jobs.tasks.recording_upload.boto3.client')
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
        upload_call = mock_s3.upload_fileobj.call_args
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
            event_type='live',
            start_datetime=timezone.now() - timedelta(hours=3),
            end_datetime=timezone.now() - timedelta(hours=1),
            timezone='Europe/Berlin',
            zoom_meeting_id='55555555555',
            zoom_join_url='https://zoom.us/j/55555555555',
            tags=['uploads'],
            required_level=0,
            status='live',
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
