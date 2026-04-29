"""Tests for YouTube upload background task - issue #111.

Covers:
- Background task: download from S3, upload to YouTube, store YouTube URL
- Error handling: missing recording, missing S3 URL, already has YouTube URL
- S3 key extraction from URL
- Description building from recording and event metadata
- Temp file cleanup after upload
"""

import os
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings
from django.utils import timezone

from events.models import Event
from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting

YOUTUBE_TEST_CLIENT_ID = 'test-yt-client-id'
YOUTUBE_TEST_CLIENT_SECRET = 'test-yt-client-secret'
YOUTUBE_TEST_REFRESH_TOKEN = 'test-yt-refresh-token'


@override_settings(
    AWS_S3_RECORDINGS_BUCKET='test-recordings-bucket',
    AWS_S3_RECORDINGS_REGION='eu-central-1',
    AWS_ACCESS_KEY_ID='test-key-id',
    AWS_SECRET_ACCESS_KEY='test-secret-key',
    YOUTUBE_CLIENT_ID=YOUTUBE_TEST_CLIENT_ID,
    YOUTUBE_CLIENT_SECRET=YOUTUBE_TEST_CLIENT_SECRET,
    YOUTUBE_REFRESH_TOKEN=YOUTUBE_TEST_REFRESH_TOKEN,
)
class UploadRecordingToYouTubeTest(TestCase):
    """Test the upload_recording_to_youtube background task."""

    def setUp(self):
        from integrations.services import youtube
        youtube.clear_token_cache()
        clear_config_cache()

        self.event = Event.objects.create(
            title='Test Workshop',
            slug='test-workshop',
            description='Learn about testing.',
            start_datetime=timezone.now() - timedelta(hours=2),
            end_datetime=timezone.now() - timedelta(hours=1),
            timezone='Europe/Berlin',
            zoom_meeting_id='12345678901',
            status='completed',
            recording_s3_url='https://test-recordings-bucket.s3.eu-central-1.amazonaws.com/recordings/2026/test-workshop.mp4',
            tags=['testing', 'workshop'],
            required_level=0,
            published=True,
        )
        self.recording = self.event

    def tearDown(self):
        clear_config_cache()

    @patch('integrations.services.youtube.upload_video')
    @patch('jobs.tasks.recordings_s3.boto3.client')
    def test_successful_upload(self, mock_boto_client, mock_upload_video):
        """Recording is downloaded from S3 and uploaded to YouTube."""
        from jobs.tasks.youtube_upload import upload_recording_to_youtube

        # Mock S3 client
        mock_s3 = MagicMock()
        mock_boto_client.return_value = mock_s3

        # Mock YouTube upload
        mock_upload_video.return_value = {
            'video_id': 'yt-vid-123',
            'youtube_url': 'https://www.youtube.com/watch?v=yt-vid-123',
        }

        result = upload_recording_to_youtube(self.recording.id)

        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['youtube_url'], 'https://www.youtube.com/watch?v=yt-vid-123')
        self.assertEqual(result['video_id'], 'yt-vid-123')

        # Verify YouTube URL stored on recording
        self.recording.refresh_from_db()
        self.assertEqual(
            self.recording.recording_url,
            'https://www.youtube.com/watch?v=yt-vid-123',
        )

    @patch('integrations.services.youtube.upload_video')
    @patch('jobs.tasks.recordings_s3.boto3.client')
    def test_upload_passes_correct_metadata(self, mock_boto_client, mock_upload_video):
        """YouTube upload receives correct title, description, and tags."""
        from jobs.tasks.youtube_upload import upload_recording_to_youtube

        mock_s3 = MagicMock()
        mock_boto_client.return_value = mock_s3

        mock_upload_video.return_value = {
            'video_id': 'vid-meta',
            'youtube_url': 'https://www.youtube.com/watch?v=vid-meta',
        }

        upload_recording_to_youtube(self.recording.id)

        mock_upload_video.assert_called_once()
        call_kwargs = mock_upload_video.call_args.kwargs
        self.assertEqual(call_kwargs['title'], 'Test Workshop')
        self.assertIn('Learn about testing.', call_kwargs['description'])
        self.assertEqual(call_kwargs['tags'], ['testing', 'workshop'])
        self.assertEqual(call_kwargs['privacy'], 'unlisted')

    @patch('integrations.services.youtube.upload_video')
    @patch('jobs.tasks.recordings_s3.boto3.client')
    def test_s3_download_called_with_correct_key(self, mock_boto_client, mock_upload_video):
        """S3 download extracts the correct key from the S3 URL."""
        from jobs.tasks.youtube_upload import upload_recording_to_youtube

        mock_s3 = MagicMock()
        mock_boto_client.return_value = mock_s3

        mock_upload_video.return_value = {
            'video_id': 'vid-s3',
            'youtube_url': 'https://www.youtube.com/watch?v=vid-s3',
        }

        upload_recording_to_youtube(self.recording.id)

        # Verify S3 download was called with correct bucket and key
        mock_s3.download_file.assert_called_once()
        download_call = mock_s3.download_file.call_args
        self.assertEqual(download_call[0][0], 'test-recordings-bucket')
        self.assertEqual(
            download_call[0][1],
            'recordings/2026/test-workshop.mp4',
        )

    @patch('integrations.services.youtube.upload_video')
    @patch('jobs.tasks.recordings_s3.boto3.client')
    def test_temp_file_cleaned_up_on_success(self, mock_boto_client, mock_upload_video):
        """Temporary file is deleted after successful upload."""
        from jobs.tasks.youtube_upload import upload_recording_to_youtube

        mock_s3 = MagicMock()
        mock_boto_client.return_value = mock_s3

        temp_paths = []

        def track_upload(**kwargs):
            # Record the file_path used
            temp_paths.append(kwargs['file_path'])
            return {
                'video_id': 'vid-cleanup',
                'youtube_url': 'https://www.youtube.com/watch?v=vid-cleanup',
            }

        mock_upload_video.side_effect = track_upload

        upload_recording_to_youtube(self.recording.id)

        # The temp file should have been cleaned up
        self.assertEqual(len(temp_paths), 1)
        self.assertFalse(os.path.exists(temp_paths[0]))

    @patch('integrations.services.youtube.upload_video')
    @patch('jobs.tasks.recordings_s3.boto3.client')
    def test_temp_file_cleaned_up_on_failure(self, mock_boto_client, mock_upload_video):
        """Temporary file is deleted even when upload fails."""
        from integrations.services.youtube import YouTubeAPIError
        from jobs.tasks.youtube_upload import upload_recording_to_youtube

        mock_s3 = MagicMock()
        mock_boto_client.return_value = mock_s3

        temp_paths = []

        def track_and_fail(**kwargs):
            temp_paths.append(kwargs['file_path'])
            # Create the temp file so cleanup can verify it's deleted
            with open(kwargs['file_path'], 'wb') as f:
                f.write(b'fake')
            raise YouTubeAPIError('Upload failed')

        mock_upload_video.side_effect = track_and_fail

        with self.assertRaises(YouTubeAPIError):
            upload_recording_to_youtube(self.recording.id)

        # The temp file should have been cleaned up
        self.assertEqual(len(temp_paths), 1)
        self.assertFalse(os.path.exists(temp_paths[0]))

    def test_missing_recording(self):
        """Task handles missing recording gracefully."""
        from jobs.tasks.youtube_upload import upload_recording_to_youtube

        result = upload_recording_to_youtube(99999)
        self.assertEqual(result['status'], 'error')
        self.assertIn('not found', result['message'])

    def test_missing_s3_url(self):
        """Task handles recording without S3 URL."""
        from jobs.tasks.youtube_upload import upload_recording_to_youtube

        self.recording.recording_s3_url = ''
        self.recording.save(update_fields=['recording_s3_url'])

        result = upload_recording_to_youtube(self.recording.id)
        self.assertEqual(result['status'], 'error')
        self.assertIn('no S3 URL', result['message'])

    def test_already_has_youtube_url(self):
        """Task skips recording that already has a YouTube URL."""
        from jobs.tasks.youtube_upload import upload_recording_to_youtube

        self.recording.recording_url = 'https://www.youtube.com/watch?v=existing'
        self.recording.save(update_fields=['recording_url'])

        result = upload_recording_to_youtube(self.recording.id)
        self.assertEqual(result['status'], 'skipped')
        self.assertIn('already has', result['message'])

    @override_settings(AWS_S3_RECORDINGS_BUCKET='')
    def test_missing_s3_bucket_config(self):
        """Task raises when S3 bucket not configured."""
        from jobs.tasks.youtube_upload import upload_recording_to_youtube

        with self.assertRaises(ValueError):
            upload_recording_to_youtube(self.recording.id)

    @patch('jobs.tasks.recordings_s3.boto3.client')
    def test_s3_download_failure_raises(self, mock_boto_client):
        """Task raises on S3 download failure to trigger django-q2 retry."""
        from botocore.exceptions import ClientError

        from jobs.tasks.youtube_upload import upload_recording_to_youtube

        mock_s3 = MagicMock()
        mock_s3.download_file.side_effect = ClientError(
            {'Error': {'Code': 'NoSuchKey', 'Message': 'Not found'}},
            'GetObject',
        )
        mock_boto_client.return_value = mock_s3

        with self.assertRaises(ClientError):
            upload_recording_to_youtube(self.recording.id)

    @patch('integrations.services.youtube.upload_video')
    @patch('jobs.tasks.recordings_s3.boto3.client')
    def test_youtube_upload_failure_raises(self, mock_boto_client, mock_upload_video):
        """Task raises on YouTube upload failure to trigger django-q2 retry."""
        from integrations.services.youtube import YouTubeAPIError
        from jobs.tasks.youtube_upload import upload_recording_to_youtube

        mock_s3 = MagicMock()
        mock_boto_client.return_value = mock_s3

        mock_upload_video.side_effect = YouTubeAPIError('Quota exceeded')

        with self.assertRaises(YouTubeAPIError):
            upload_recording_to_youtube(self.recording.id)

        # youtube_url should NOT be set on the recording
        self.recording.refresh_from_db()
        self.assertEqual(self.recording.recording_url, '')

    @patch('integrations.services.youtube.upload_video')
    @patch('jobs.tasks.recordings_s3.boto3.client')
    def test_uses_studio_config_for_recordings_s3_download(
        self,
        mock_boto_client,
        mock_upload_video,
    ):
        """Studio IntegrationSetting values drive the S3 download path."""
        from jobs.tasks.youtube_upload import upload_recording_to_youtube

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

        self.recording.recording_s3_url = (
            'https://studio-recordings-bucket.s3.us-west-2.amazonaws.com/'
            'recordings/2026/test-workshop.mp4'
        )
        self.recording.save(update_fields=['recording_s3_url'])

        mock_s3 = MagicMock()
        mock_boto_client.return_value = mock_s3
        mock_upload_video.return_value = {
            'video_id': 'studio-config',
            'youtube_url': 'https://www.youtube.com/watch?v=studio-config',
        }

        result = upload_recording_to_youtube(self.recording.id)

        self.assertEqual(result['status'], 'ok')
        mock_boto_client.assert_called_once_with(
            's3',
            region_name='us-west-2',
            aws_access_key_id='studio-access-key',
            aws_secret_access_key='studio-secret-key',
        )
        mock_s3.download_file.assert_called_once()
        download_call = mock_s3.download_file.call_args
        self.assertEqual(download_call[0][0], 'studio-recordings-bucket')
        self.assertEqual(download_call[0][1], 'recordings/2026/test-workshop.mp4')


class BuildDescriptionTest(TestCase):
    """Test the _build_description helper."""

    def test_description_includes_recording_description(self):
        from jobs.tasks.youtube_upload import _build_description

        recording = Event.objects.create(
            title='Desc Test',
            slug='desc-test',
            start_datetime=timezone.now(), status='completed',
            description='My recording description.',
        )
        desc = _build_description(recording)
        self.assertIn('My recording description.', desc)

    def test_description_includes_event_description(self):
        """The description includes the event's own description."""
        from jobs.tasks.youtube_upload import _build_description

        event = Event.objects.create(
            title='Event Desc Test',
            slug='event-desc-test',
            description='Event details here.',
            start_datetime=timezone.now(), status='completed',
        )
        desc = _build_description(event)
        self.assertIn('Event details here.', desc)

    def test_description_includes_date(self):
        from jobs.tasks.youtube_upload import _build_description

        recording = Event.objects.create(
            title='Date Test',
            slug='date-test',
            start_datetime=timezone.make_aware(timezone.datetime(2026, 3, 15, 12, 0)), status='completed',
        )
        desc = _build_description(recording)
        self.assertIn('March 15, 2026', desc)

    def test_description_includes_learning_objectives(self):
        from jobs.tasks.youtube_upload import _build_description

        recording = Event.objects.create(
            title='Objectives Test',
            slug='objectives-test',
            start_datetime=timezone.now(), status='completed',
            learning_objectives=['Build an AI agent', 'Deploy to production'],
        )
        desc = _build_description(recording)
        self.assertIn('What you will learn:', desc)
        self.assertIn('- Build an AI agent', desc)
        self.assertIn('- Deploy to production', desc)

    def test_description_includes_site_link(self):
        from jobs.tasks.youtube_upload import _build_description

        recording = Event.objects.create(
            title='Link Test',
            slug='link-test',
            start_datetime=timezone.now(), status='completed',
        )
        desc = _build_description(recording)
        self.assertIn('AI Shipping Labs', desc)
        self.assertIn('https://aishippinglabs.com', desc)

    def test_description_avoids_duplicate_event_description(self):
        from jobs.tasks.youtube_upload import _build_description

        Event.objects.create(
            title='Same Desc Event',
            slug='same-desc-event',
            description='Same description text.',
            start_datetime=timezone.now(),
        )
        recording = Event.objects.create(
            title='Same Desc Recording',
            slug='same-desc-recording',
            start_datetime=timezone.now(), status='completed',
            description='Same description text.',
            
        )
        desc = _build_description(recording)
        # Should only appear once
        self.assertEqual(desc.count('Same description text.'), 1)


class ExtractS3KeyTest(TestCase):
    """Test the _extract_s3_key helper."""

    def test_standard_s3_url(self):
        from jobs.tasks.youtube_upload import _extract_s3_key

        key = _extract_s3_key(
            'https://my-bucket.s3.eu-central-1.amazonaws.com/recordings/2026/video.mp4',
            'my-bucket',
            'eu-central-1',
        )
        self.assertEqual(key, 'recordings/2026/video.mp4')

    def test_fallback_parsing(self):
        from jobs.tasks.youtube_upload import _extract_s3_key

        key = _extract_s3_key(
            'https://other-format.s3.amazonaws.com/path/to/file.mp4',
            'different-bucket',
            'us-east-1',
        )
        self.assertEqual(key, 'path/to/file.mp4')
