from datetime import timezone as datetime_timezone
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings
from django.utils import timezone

from events.models import Event
from integrations.config import clear_config_cache
from jobs.tasks.recordings_s3 import (
    RecordingsS3Config,
    build_recording_s3_key,
    build_recording_s3_url,
    extract_s3_key,
    get_recordings_s3_config,
    upload_recording_mp4,
)


class RecordingsS3HelperTest(TestCase):
    def tearDown(self):
        clear_config_cache()

    @override_settings(
        AWS_S3_RECORDINGS_BUCKET='helper-bucket',
        AWS_S3_RECORDINGS_REGION='',
        AWS_ACCESS_KEY_ID='helper-key',
        AWS_SECRET_ACCESS_KEY='helper-secret',
    )
    def test_config_uses_default_region_when_setting_empty(self):
        clear_config_cache()

        config = get_recordings_s3_config()

        self.assertEqual(config.bucket, 'helper-bucket')
        self.assertEqual(config.region, 'eu-central-1')
        self.assertEqual(config.access_key_id, 'helper-key')
        self.assertEqual(config.secret_access_key, 'helper-secret')

    def test_key_and_url_format_match_recording_tasks(self):
        event = Event.objects.create(
            title='Helper Event',
            slug='helper-event',
            start_datetime=timezone.datetime(2026, 4, 1, tzinfo=datetime_timezone.utc),
        )

        key = build_recording_s3_key(event)
        url = build_recording_s3_url('helper-bucket', 'eu-central-1', key)

        self.assertEqual(key, 'recordings/2026/helper-event.mp4')
        self.assertEqual(
            url,
            'https://helper-bucket.s3.eu-central-1.amazonaws.com/recordings/2026/helper-event.mp4',
        )

    @patch('jobs.tasks.recordings_s3.boto3.client')
    def test_upload_uses_mp4_content_type(self, mock_boto_client):
        config = RecordingsS3Config(
            bucket='helper-bucket',
            region='eu-central-1',
            access_key_id='key',
            secret_access_key='secret',
        )
        mock_s3 = MagicMock()
        mock_boto_client.return_value = mock_s3

        url = upload_recording_mp4(b'data', config, 'recordings/2026/helper.mp4')

        self.assertEqual(
            url,
            'https://helper-bucket.s3.eu-central-1.amazonaws.com/recordings/2026/helper.mp4',
        )
        self.assertEqual(
            mock_s3.upload_fileobj.call_args.kwargs['ExtraArgs']['ContentType'],
            'video/mp4',
        )

    def test_extract_s3_key_keeps_standard_and_fallback_behavior(self):
        standard = extract_s3_key(
            'https://helper-bucket.s3.eu-central-1.amazonaws.com/recordings/2026/helper.mp4',
            'helper-bucket',
            'eu-central-1',
        )
        fallback = extract_s3_key(
            'https://other-format.s3.amazonaws.com/path/to/file.mp4',
            'different-bucket',
            'us-east-1',
        )

        self.assertEqual(standard, 'recordings/2026/helper.mp4')
        self.assertEqual(fallback, 'path/to/file.mp4')
