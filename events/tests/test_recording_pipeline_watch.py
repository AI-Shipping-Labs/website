"""Recording pipeline watch: Zoom MP4 poll, bound, stall alert (intake 1f596393).

Event 69 (Evals in CI) got its host "Recording ready for review" email
about 19 hours after the recording ended, because the upload only started
on ``recording.transcript_completed``. These tests pin the replacement:
the platform polls Zoom for a finished MP4, starts the upload without
waiting for the transcript, sends the host email right after the upload,
and alerts staff when that does not happen within the documented bound.
"""

from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

from django.test import TestCase
from django.utils import timezone

from email_app.models import EmailLog
from events.models import Event
from events.services.recording_pipeline_watch import (
    RECORDING_POLL_SOURCE,
    RECORDING_READY_BOUND,
    check_recording_pipeline_stalls,
    poll_zoom_recordings,
    recording_matches_event,
)
from integrations.services.zoom import ZoomAPIError
from notifications.services.staff_slack import SlackPostResult

WATCH = 'events.services.recording_pipeline_watch'
ALERTS = 'events.services.recording_pipeline_alerts'


def make_event(**kwargs):
    now = timezone.now()
    defaults = {
        'title': 'Evals in CI',
        'slug': f'evals-in-ci-{uuid4().hex[:8]}',
        'start_datetime': now - timedelta(minutes=75),
        'end_datetime': now + timedelta(minutes=45),
        'timezone': 'Europe/Berlin',
        'status': 'upcoming',
        'zoom_meeting_id': '81234567890',
        'host_email': 'host@example.com',
    }
    defaults.update(kwargs)
    return Event.objects.create(**defaults)


def zoom_listing(event, *, status='completed', start=None, with_transcript=False):
    start = start or event.start_datetime + timedelta(seconds=46)
    files = [{
        'recording_type': 'shared_screen_with_speaker_view',
        'file_type': 'MP4',
        'status': status,
        'download_url': 'https://zoom.us/rec/download/mp4-file',
    }]
    if with_transcript:
        files.append({
            'recording_type': 'audio_transcript',
            'file_type': 'TRANSCRIPT',
            'status': 'completed',
            'download_url': 'https://zoom.us/rec/download/vtt-file',
        })
    return {
        'start_time': start.strftime('%Y-%m-%dT%H:%M:%SZ'),
        'recording_files': files,
    }


@patch('events.services.recording_upload.enqueue_recording_upload_task', return_value='task-1')
class ZoomRecordingPollTest(TestCase):
    def test_finished_mp4_starts_upload_without_transcript(self, mock_enqueue):
        """Video upload starts from the MP4 alone; no transcript needed."""
        event = make_event()
        with patch(
            'integrations.services.zoom.get_meeting_recordings',
            return_value=zoom_listing(event),
        ):
            result = poll_zoom_recordings()

        self.assertEqual(result['queued'], 1)
        event.refresh_from_db()
        self.assertEqual(event.recording_zoom_download_url, 'https://zoom.us/rec/download/mp4-file')
        self.assertIsNotNone(event.recording_zoom_available_at)
        self.assertIsNotNone(event.recording_upload_enqueued_at)
        self.assertEqual(event.transcript_url, '')
        mock_enqueue.assert_called_once()
        self.assertEqual(mock_enqueue.call_args.kwargs['source'], RECORDING_POLL_SOURCE)

    def test_transcript_url_is_stored_when_zoom_already_has_it(self, mock_enqueue):
        event = make_event()
        with patch(
            'integrations.services.zoom.get_meeting_recordings',
            return_value=zoom_listing(event, with_transcript=True),
        ):
            poll_zoom_recordings()

        event.refresh_from_db()
        self.assertEqual(event.transcript_url, 'https://zoom.us/rec/download/vtt-file')
        mock_enqueue.assert_called_once()

    def test_gallery_only_completed_mp4_starts_upload(self, mock_enqueue):
        event = make_event()
        listing = zoom_listing(event)
        listing['recording_files'][0]['recording_type'] = 'gallery_view'
        with patch('integrations.services.zoom.get_meeting_recordings', return_value=listing):
            result = poll_zoom_recordings()
        self.assertEqual(result['queued'], 1)
        mock_enqueue.assert_called_once()

    def test_processing_mp4_is_not_used(self, mock_enqueue):
        event = make_event()
        with patch(
            'integrations.services.zoom.get_meeting_recordings',
            return_value=zoom_listing(event, status='processing'),
        ):
            result = poll_zoom_recordings()

        self.assertEqual(result['queued'], 0)
        self.assertEqual(result['not_ready'], 1)
        event.refresh_from_db()
        self.assertEqual(event.recording_zoom_download_url, '')
        self.assertIsNone(event.recording_zoom_available_at)
        mock_enqueue.assert_not_called()

    def test_zoom_404_means_not_ready(self, mock_enqueue):
        make_event()
        with patch(
            'integrations.services.zoom.get_meeting_recordings',
            side_effect=ZoomAPIError('missing', status_code=404),
        ):
            result = poll_zoom_recordings()

        self.assertEqual(result, {'examined': 1, 'queued': 0, 'not_ready': 1, 'errors': 0})
        mock_enqueue.assert_not_called()

    def test_zoom_error_is_counted_and_logged_not_raised(self, mock_enqueue):
        make_event()
        with patch(
            'integrations.services.zoom.get_meeting_recordings',
            side_effect=ZoomAPIError('boom', status_code=500),
        ), self.assertLogs(WATCH, level='WARNING'):
            result = poll_zoom_recordings()

        self.assertEqual(result['errors'], 1)
        mock_enqueue.assert_not_called()

    def test_recording_from_an_earlier_occurrence_is_ignored(self, mock_enqueue):
        """A recurring meeting ID must not attach last week's video."""
        event = make_event()
        listing = zoom_listing(event, start=event.start_datetime - timedelta(days=7))
        with patch('integrations.services.zoom.get_meeting_recordings', return_value=listing):
            result = poll_zoom_recordings()

        self.assertEqual(result['queued'], 0)
        event.refresh_from_db()
        self.assertEqual(event.recording_zoom_download_url, '')
        mock_enqueue.assert_not_called()

    def test_only_candidate_events_are_polled(self, mock_enqueue):
        now = timezone.now()
        make_event(zoom_meeting_id='')
        make_event(status='cancelled')
        make_event(start_datetime=now - timedelta(minutes=5))
        make_event(start_datetime=now - timedelta(days=4), end_datetime=now - timedelta(days=4))
        make_event(recording_s3_url='https://bucket.s3.amazonaws.com/x.mp4', transcript_text='stored')
        with patch('integrations.services.zoom.get_meeting_recordings') as mock_list:
            result = poll_zoom_recordings()

        self.assertEqual(result['examined'], 0)
        mock_list.assert_not_called()
        mock_enqueue.assert_not_called()

    def test_poll_is_idempotent_once_the_mp4_is_stored(self, mock_enqueue):
        event = make_event()
        with patch(
            'integrations.services.zoom.get_meeting_recordings',
            return_value=zoom_listing(event),
        ) as mock_list:
            poll_zoom_recordings()
            second = poll_zoom_recordings()

        self.assertEqual(second['examined'], 1)
        self.assertEqual(mock_list.call_count, 2)
        mock_enqueue.assert_called_once()


class RecordingMatchesEventTest(TestCase):
    def test_window_bounds(self):
        event = make_event(end_datetime=timezone.now() + timedelta(minutes=45))
        start = event.start_datetime
        self.assertFalse(recording_matches_event({}, event))
        self.assertTrue(recording_matches_event(zoom_listing(event), event))
        self.assertTrue(recording_matches_event(
            zoom_listing(event, start=start - timedelta(hours=2)), event,
        ))
        self.assertFalse(recording_matches_event(
            zoom_listing(event, start=start - timedelta(hours=4)), event,
        ))
        self.assertFalse(recording_matches_event({'start_time': 'garbage'}, event))


class RecordingStallAlertTest(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.slack_enabled = patch('community.slack_config.slack_api_enabled', return_value=True)
        self.slack_enabled.start()
        self.addCleanup(self.slack_enabled.stop)
        self.channel = patch(
            'notifications.services.staff_slack.staff_alert_channel_id', return_value='C-STAFF',
        )
        self.channel.start()
        self.addCleanup(self.channel.stop)

    def _stalled_event(self, minutes_ago, **kwargs):
        return make_event(
            recording_zoom_download_url='https://zoom.us/rec/download/mp4-file',
            recording_zoom_available_at=self.now - timedelta(minutes=minutes_ago),
            **kwargs,
        )

    @patch('notifications.services.staff_slack.post_staff_slack_message')
    def test_upload_past_bound_posts_one_loud_staff_alert(self, mock_post):
        mock_post.return_value = SlackPostResult(ok=True)
        event = self._stalled_event(65)

        first = check_recording_pipeline_stalls(now=self.now)
        second = check_recording_pipeline_stalls(now=self.now + timedelta(minutes=5))

        self.assertEqual(first, {'alerted': 1, 'failed': 0})
        self.assertEqual(second, {'alerted': 0, 'failed': 0})
        self.assertEqual(mock_post.call_count, 1)
        channel, message = mock_post.call_args.args
        self.assertEqual(channel, 'C-STAFF')
        self.assertIn('Recording pipeline stalled', message['text'])
        self.assertIn('S3 upload', message['text'])
        self.assertIn(f'/studio/events/{event.pk}/edit', message['text'])
        self.assertNotIn('zoom.us/rec/download', message['text'])
        event.refresh_from_db()
        self.assertEqual(event.recording_stall_alerted_at, self.now)

    @patch('notifications.services.staff_slack.post_staff_slack_message')
    def test_within_bound_no_alert(self, mock_post):
        self._stalled_event(int(RECORDING_READY_BOUND.total_seconds() // 60) - 1)

        self.assertEqual(check_recording_pipeline_stalls(now=self.now), {'alerted': 0, 'failed': 0})
        mock_post.assert_not_called()

    @patch('notifications.services.staff_slack.post_staff_slack_message')
    @patch('email_app.services.relay_campaigns.get_native_campaign_stats', return_value=None)
    def test_uploaded_but_no_host_email_alerts_on_email_step(self, _stats, mock_post):
        mock_post.return_value = SlackPostResult(ok=True)
        self._stalled_event(90, recording_s3_url='https://bucket.s3.amazonaws.com/x.mp4')

        check_recording_pipeline_stalls(now=self.now)

        self.assertIn('Recording ready for review', mock_post.call_args.args[1]['text'])

    @patch('notifications.services.staff_slack.post_staff_slack_message')
    def test_earlier_confirmed_delivery_is_healthy(self, mock_post):
        event = self._stalled_event(90, recording_s3_url='https://bucket.s3.amazonaws.com/x.mp4', recording_ready_completed_at=self.now - timedelta(minutes=35))
        EmailLog.objects.create(
            event=event, recipient_email='host@example.com',
            email_type='event_recording_ready', subject='Recording ready for review',
        )

        self.assertEqual(check_recording_pipeline_stalls(now=self.now), {'alerted': 0, 'failed': 0})
        mock_post.assert_not_called()

    @patch(f'{ALERTS}.send_mail', return_value=1)
    @patch('notifications.services.staff_slack.post_staff_slack_message')
    def test_slack_failure_falls_back_to_dev_email(self, mock_post, mock_mail):
        mock_post.return_value = SlackPostResult(ok=False, error='channel_not_found')
        event = self._stalled_event(65)

        with self.assertLogs(ALERTS, level='ERROR'):
            result = check_recording_pipeline_stalls(now=self.now)

        self.assertEqual(result['alerted'], 1)
        self.assertEqual(mock_mail.call_args.kwargs['recipient_list'], ['dev@dtcdev.click'])
        event.refresh_from_db()
        self.assertIsNotNone(event.recording_stall_alerted_at)

    @patch(f'{ALERTS}.send_mail', side_effect=RuntimeError('relay down'))
    @patch('notifications.services.staff_slack.post_staff_slack_message')
    def test_all_routes_failing_retries_next_pass(self, mock_post, _mail):
        """No silent drop: the marker stays empty so the next pass retries."""
        mock_post.return_value = SlackPostResult(ok=False, error='invalid_auth')
        event = self._stalled_event(65)

        with self.assertLogs(ALERTS, level='ERROR'):
            result = check_recording_pipeline_stalls(now=self.now)

        self.assertEqual(result, {'alerted': 0, 'failed': 1})
        event.refresh_from_db()
        self.assertIsNone(event.recording_stall_alerted_at)

        mock_post.return_value = SlackPostResult(ok=True)
        retry = check_recording_pipeline_stalls(now=self.now + timedelta(minutes=5))
        self.assertEqual(retry['alerted'], 1)
