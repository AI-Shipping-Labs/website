"""A local submission log is never mistaken for Relay send completion."""

from datetime import timedelta
from unittest.mock import ANY, patch

from django.test import TestCase
from django.utils import timezone

from email_app.models import EmailLog
from events.services.recording_pipeline_watch import check_recording_pipeline_stalls, refresh_host_notice
from events.tests.test_recording_pipeline_watch import make_event


class RecordingDeliveryConfirmationTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.event = make_event(
            recording_zoom_available_at=self.now - timedelta(minutes=30),
            recording_s3_url='https://private.example.com/recording.mp4',
        )
        EmailLog.objects.create(
            event=self.event, recipient_email='host@example.com',
            email_type='event_recording_ready', subject='Submitted only',
        )

    @patch('email_app.services.relay_campaigns.get_native_campaign_stats')
    def test_pending_relay_campaign_does_not_complete_stage(self, stats):
        stats.return_value = {'sent_count': 0, 'pending_count': 1}
        refresh_host_notice(self.event.pk)
        self.event.refresh_from_db()
        self.assertIsNone(self.event.recording_ready_completed_at)

    @patch('email_app.services.relay_campaigns.get_native_campaign_stats')
    def test_confirmed_relay_send_completes_stage(self, stats):
        stats.return_value = {'sent_count': 1, 'pending_count': 0}
        refresh_host_notice(self.event.pk)
        self.event.refresh_from_db()
        self.assertIsNotNone(self.event.recording_ready_completed_at)
        self.assertEqual(stats.call_args.args[0], f'event-recording-ready-{self.event.pk}')

    @patch('events.services.recording_pipeline_alerts.deliver_pipeline_alert', return_value=True)
    @patch('email_app.services.relay_campaigns.get_native_campaign_stats')
    def test_first_confirmation_after_bound_still_alerts(self, stats, alert):
        self.event.recording_zoom_available_at = self.now - timedelta(minutes=65)
        self.event.save()
        stats.return_value = {'sent_count': 1, 'pending_count': 0}
        refresh_host_notice(self.event.pk)
        result = check_recording_pipeline_stalls()
        self.assertEqual(result['alerted'], 1)
        alert.assert_called_once_with(
            self.event, f'Recording pipeline stalled: {self.event.title} (event {self.event.pk})', ANY,
        )
        self.assertIn('not confirmed complete within the bound', alert.call_args.args[2])

    @patch('events.services.recording_pipeline_alerts.deliver_pipeline_alert', return_value=True)
    def test_healthy_old_rows_cannot_starve_later_stall(self, alert):
        for index in range(21):
            make_event(
                recording_zoom_available_at=self.now - timedelta(hours=3),
                recording_ready_completed_at=self.now - timedelta(hours=2, minutes=30),
                recording_s3_url=f'https://private.example.com/healthy-{index}.mp4',
            )
        self.event.recording_zoom_available_at = self.now - timedelta(minutes=65)
        self.event.save()
        result = check_recording_pipeline_stalls()
        self.assertEqual(result['alerted'], 1)
        self.assertEqual(alert.call_args.args[0].pk, self.event.pk)


    @patch('events.services.recording_pipeline_alerts.deliver_pipeline_alert', return_value=True)
    def test_unknown_delivery_state_allows_only_four_minutes_observation_grace(self, alert):
        self.event.recording_zoom_available_at = self.now - timedelta(minutes=63)
        self.event.save()
        self.assertEqual(check_recording_pipeline_stalls(now=self.now)['alerted'], 0)
        self.assertEqual(check_recording_pipeline_stalls(now=self.now + timedelta(minutes=1))['alerted'], 1)
