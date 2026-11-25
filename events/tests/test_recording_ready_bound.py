"""Mocked Zoom MP4 to S3 and host email bounded flow."""

from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from email_app.models import EmailLog
from events.services.recording_pipeline_watch import (
    RECORDING_POLL_INTERVAL,
    RECORDING_READY_BOUND,
    check_recording_pipeline_stalls,
    poll_zoom_recordings,
    refresh_host_notice,
)
from events.tests.test_recording_pipeline_watch import make_event, zoom_listing
from integrations.config import clear_config_cache


@override_settings(
    AWS_S3_RECORDINGS_BUCKET='test-recordings-bucket',
    AWS_S3_RECORDINGS_REGION='eu-central-1',
    AWS_ACCESS_KEY_ID='test-key-id',
    AWS_SECRET_ACCESS_KEY='test-secret-key',
    ZOOM_CLIENT_ID='test-client-id',
    ZOOM_CLIENT_SECRET='test-client-secret',
    ZOOM_ACCOUNT_ID='test-account-id',
)
class RecordingReadyBoundSimulationTest(TestCase):
    """Simulated Zoom MP4 -> poll -> upload -> host email within the bound."""

    def setUp(self):
        from integrations.services import zoom

        zoom.clear_token_cache()
        clear_config_cache()
        self.addCleanup(clear_config_cache)

    @patch('notifications.services.staff_slack.post_staff_slack_message')
    @patch('jobs.tasks.recordings_s3.boto3.client')
    @patch('jobs.tasks.recording_upload.requests.get')
    @patch('integrations.services.zoom.requests.post')
    def test_host_email_lands_within_bound_of_mp4_availability(
        self, mock_token, mock_download, mock_boto, mock_slack,
    ):
        from jobs.tasks.recording_upload import upload_recording_to_s3

        token = MagicMock(status_code=200)
        token.json.return_value = {'access_token': 'test-token', 'expires_in': 3600}
        mock_token.return_value = token
        download = MagicMock(status_code=200)
        download.iter_content.return_value = [b'fake-video']
        mock_download.return_value = download
        mock_boto.return_value = MagicMock()

        event = make_event()
        queued = []
        with patch(
            'integrations.services.zoom.get_meeting_recordings',
            return_value=zoom_listing(event),
        ), patch(
            'events.services.recording_upload.enqueue_recording_upload_task',
            side_effect=lambda ev, url, source: queued.append((ev.pk, url)),
        ):
            # Zoom lists the MP4 as finished; the next 5-minute poll sees it.
            poll_zoom_recordings()

        self.assertEqual(len(queued), 1)
        # The worker runs the queued upload; the host email follows it.
        result = upload_recording_to_s3(*queued[0])

        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['host_notification_status'], 'sent')
        event.refresh_from_db()
        log = EmailLog.objects.get(event=event, email_type='event_recording_ready')
        elapsed = log.sent_at - event.recording_zoom_available_at
        self.assertLess(elapsed, RECORDING_READY_BOUND)
        self.assertLessEqual(RECORDING_POLL_INTERVAL, timedelta(minutes=5))

        with patch('email_app.services.relay_campaigns.get_native_campaign_stats', return_value={'sent_count': 1, 'pending_count': 0}):
            refresh_host_notice(event.pk)
        # A watch pass after the bound sees a healthy event and stays quiet.
        later = event.recording_zoom_available_at + RECORDING_READY_BOUND + timedelta(minutes=1)
        self.assertEqual(check_recording_pipeline_stalls(now=later), {'alerted': 0, 'failed': 0})
        mock_slack.assert_not_called()
