"""API contract for transcript sync and recap redraft (issue #1597)."""

import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import Token
from events.models import Event
from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting

User = get_user_model()


def _past_start_end():
    now = timezone.now()
    return now - timedelta(hours=3), now - timedelta(hours=1)


class EventTranscriptSyncApiTest(TestCase):
    def setUp(self):
        clear_config_cache()
        self.addCleanup(clear_config_cache)
        self.staff = User.objects.create_user(
            email='transcript-api-staff@example.com',
            password='pw',
            is_staff=True,
        )
        self.staff_token = Token.objects.create(user=self.staff, name='sync')
        start, end = _past_start_end()
        self.event = Event.objects.create(
            title='Transcript API event',
            slug='transcript-api-event',
            start_datetime=start,
            end_datetime=end,
            status='completed',
            origin='github',
            source_repo='AI-Shipping-Labs/content',
            source_path='events/transcript.md',
            zoom_meeting_id='98765432109',
            transcript_url='https://zoom.us/rec/download/api.vtt',
        )

    def _auth(self, key=None):
        token = self.staff_token.key if key is None else key
        return {'HTTP_AUTHORIZATION': f'Token {token}'}

    def test_get_includes_transcript_fields_and_derived_status(self):
        Event.objects.filter(pk=self.event.pk).update(
            transcript_text='A stored transcript.',
            transcript_s3_url=(
                'https://private-recordings.s3.eu-central-1.amazonaws.com/'
                'recordings/2026/transcript-api-event.vtt'
            ),
        )
        response = self.client.get(
            f'/api/events/{self.event.slug}',
            **self._auth(),
        )
        body = response.json()
        self.assertEqual(
            body['transcript_url'],
            'https://zoom.us/rec/download/api.vtt',
        )
        self.assertEqual(body['transcript_text'], 'A stored transcript.')
        self.assertEqual(
            body['transcript_s3_url'],
            'https://private-recordings.s3.eu-central-1.amazonaws.com/'
            'recordings/2026/transcript-api-event.vtt',
        )
        self.assertEqual(body['transcript_status'], 'stored')

    def test_archive_url_is_read_only(self):
        Event.objects.filter(pk=self.event.pk).update(
            origin='api',
            source_repo='',
            source_path='',
        )
        response = self.client.patch(
            f'/api/events/{self.event.slug}',
            data=json.dumps({
                'transcript_s3_url': 'https://attacker.example/raw.vtt',
            }),
            content_type='application/json',
            **self._auth(),
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()['code'], 'read_only_field')
        self.event.refresh_from_db()
        self.assertEqual(self.event.transcript_s3_url, '')

    def test_get_reports_unavailable_after_terminal_marker(self):
        Event.objects.filter(pk=self.event.pk).update(
            transcript_unavailable_at=timezone.now(),
        )
        body = self.client.get(
            f'/api/events/{self.event.slug}',
            **self._auth(),
        ).json()
        self.assertEqual(body['transcript_status'], 'unavailable')

    @patch('jobs.tasks.helpers.q_async_task')
    def test_sync_without_token_is_401(self, mock_q_async):
        response = self.client.post(
            f'/api/events/{self.event.slug}/sync-transcript',
        )
        self.assertEqual(response.status_code, 401)
        mock_q_async.assert_not_called()

    def test_sync_unknown_slug_is_404(self):
        response = self.client.post(
            '/api/events/missing-transcript/sync-transcript',
            **self._auth(),
        )
        self.assertEqual(response.status_code, 404)

    def test_sync_malformed_body_is_400(self):
        response = self.client.post(
            f'/api/events/{self.event.slug}/sync-transcript',
            data='{not json',
            content_type='application/json',
            **self._auth(),
        )
        self.assertEqual(response.status_code, 400)

    def test_sync_rejects_non_boolean_redraft(self):
        response = self.client.post(
            f'/api/events/{self.event.slug}/sync-transcript',
            data=json.dumps({'redraft': 'false'}),
            content_type='application/json',
            **self._auth(),
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            response.json()['details']['redraft'],
            'Must be a boolean.',
        )

    @patch('jobs.tasks.helpers.q_async_task')
    def test_sync_queues_transcript_task(self, mock_q_async):
        mock_q_async.return_value = 'sync-task-1'
        response = self.client.post(
            f'/api/events/{self.event.slug}/sync-transcript',
            data=json.dumps({}),
            content_type='application/json',
            **self._auth(),
        )
        body = response.json()
        self.assertTrue(body['transcript_queued'])
        self.assertEqual(body['task_id'], 'sync-task-1')
        self.assertEqual(body['transcript_status'], 'waiting')
        self.assertIsNone(body['recap_queued'])
        self.assertFalse(body['redraft'])
        self.assertEqual(mock_q_async.call_args[0][0],
                         'jobs.tasks.recording_transcript.transcribe_recording')

    @patch('jobs.tasks.helpers.q_async_task')
    def test_sync_noop_when_transcript_already_stored(self, mock_q_async):
        Event.objects.filter(pk=self.event.pk).update(
            transcript_text='Already stored.',
            transcript_s3_url=(
                'https://private-recordings.s3.eu-central-1.amazonaws.com/'
                'recordings/2026/transcript-api-event.vtt'
            ),
        )
        response = self.client.post(
            f'/api/events/{self.event.slug}/sync-transcript',
            data=json.dumps({}),
            content_type='application/json',
            **self._auth(),
        )
        body = response.json()
        self.assertFalse(body['transcript_queued'])
        self.assertIsNone(body['task_id'])
        self.assertEqual(body['transcript_status'], 'stored')
        mock_q_async.assert_not_called()

    @patch(
        'api.views.events.refresh_transcript_from_zoom',
        return_value={'refreshed': True, 'transcript_url': False},
    )
    @patch('jobs.tasks.helpers.q_async_task', return_value='archive-task')
    def test_sync_recovers_text_only_row_without_clobbering_durable_content(
        self, mock_q_async, mock_refresh,
    ):
        Event.objects.filter(pk=self.event.pk).update(
            transcript_text='Keep the durable transcript.',
            transcript_s3_url='',
            recap_notes='Keep the operator recap.',
        )

        response = self.client.post(
            f'/api/events/{self.event.slug}/sync-transcript',
            data=json.dumps({}),
            content_type='application/json',
            **self._auth(),
        )

        body = response.json()
        self.assertTrue(body['transcript_queued'])
        self.assertEqual(body['task_id'], 'archive-task')
        self.assertEqual(mock_refresh.call_count, 1)
        self.assertEqual(mock_q_async.call_count, 1)
        self.assertEqual(
            mock_q_async.call_args.args[0],
            'jobs.tasks.recording_transcript.transcribe_recording',
        )
        self.event.refresh_from_db()
        self.assertEqual(
            self.event.transcript_text,
            'Keep the durable transcript.',
        )
        self.assertEqual(self.event.recap_notes, 'Keep the operator recap.')
        self.assertEqual(self.event.transcript_s3_url, '')

    @patch('jobs.tasks.helpers.q_async_task')
    def test_sync_backfills_missed_vtt_via_zoom_relist(self, mock_q_async):
        """Webhook-missed transcript is picked up from the Zoom API (#1597)."""
        Event.objects.filter(pk=self.event.pk).update(
            transcript_url='',
            recording_zoom_download_url='',
        )
        mock_q_async.return_value = 'sync-task-2'
        with patch(
            'integrations.services.zoom.get_meeting_recordings',
            return_value={
                'recording_files': [
                    {
                        'recording_type': 'audio_transcript',
                        'download_url': 'https://zoom.us/rec/download/found.vtt',
                    },
                    {
                        'recording_type': 'shared_screen',
                        'download_url': 'https://zoom.us/rec/download/mp4',
                    },
                ],
            },
        ):
            response = self.client.post(
                f'/api/events/{self.event.slug}/sync-transcript',
                data=json.dumps({}),
                content_type='application/json',
                **self._auth(),
            )

        body = response.json()
        self.assertEqual(body['zoom_refresh'], 'transcript_found')
        self.event.refresh_from_db()
        self.assertEqual(
            self.event.transcript_url,
            'https://zoom.us/rec/download/found.vtt',
        )
        # No S3 recording and no stored download URL: the re-list also
        # backfills the MP4 upload chain.
        self.assertEqual(
            self.event.recording_zoom_download_url,
            'https://zoom.us/rec/download/mp4',
        )
        queued_funcs = [call[0][0] for call in mock_q_async.call_args_list]
        self.assertIn(
            'jobs.tasks.recording_upload.upload_recording_to_s3',
            queued_funcs,
        )
        self.assertIn(
            'jobs.tasks.recording_transcript.transcribe_recording',
            queued_funcs,
        )

    @patch('api.views.events.llm_is_enabled', return_value=False)
    def test_sync_redraft_with_llm_disabled_is_422(self, mock_llm_enabled):
        response = self.client.post(
            f'/api/events/{self.event.slug}/sync-transcript',
            data=json.dumps({'redraft': True}),
            content_type='application/json',
            **self._auth(),
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()['code'], 'llm_not_configured')

    @patch('api.views.events.llm_is_enabled', return_value=True)
    @patch('jobs.tasks.helpers.q_async_task')
    def test_sync_redraft_queues_forced_recap(
        self, mock_q_async, mock_llm_enabled,
    ):
        mock_q_async.return_value = 'redraft-task'
        Event.objects.filter(pk=self.event.pk).update(
            transcript_text='Stored transcript.',
            recap_notes='Old draft',
        )
        response = self.client.post(
            f'/api/events/{self.event.slug}/sync-transcript',
            data=json.dumps({'redraft': True}),
            content_type='application/json',
            **self._auth(),
        )
        body = response.json()
        self.assertTrue(body['redraft'])
        self.assertTrue(body['recap_queued'])
        self.assertTrue(body['transcript_queued'])
        # Redraft passes force=True through to the transcript task.
        self.assertEqual(mock_q_async.call_args[0][2], True)

    @patch('jobs.tasks.helpers.q_async_task')
    def test_sync_works_even_when_ingest_toggle_off(self, mock_q_async):
        """Explicit operator sync bypasses RECORDING_TRANSCRIPT_INGEST_ENABLED."""
        IntegrationSetting.objects.update_or_create(
            key='RECORDING_TRANSCRIPT_INGEST_ENABLED',
            defaults={'value': 'false', 'group': 's3_recordings'},
        )
        clear_config_cache()
        mock_q_async.return_value = 'manual-task'
        response = self.client.post(
            f'/api/events/{self.event.slug}/sync-transcript',
            data=json.dumps({}),
            content_type='application/json',
            **self._auth(),
        )
        self.assertTrue(response.json()['transcript_queued'])


class EventTranscriptToggleTest(TestCase):
    """The ingest toggle gates the automatic webhook enqueue path only."""

    def setUp(self):
        clear_config_cache()
        self.addCleanup(clear_config_cache)
        self.staff = User.objects.create_user(
            email='toggle-staff@example.com',
            password='pw',
            is_staff=True,
        )
        self.token = Token.objects.create(user=self.staff, name='toggle')
        start, end = _past_start_end()
        self.event = Event.objects.create(
            title='Toggle event',
            slug='toggle-event',
            start_datetime=start,
            end_datetime=end,
            status='completed',
            zoom_meeting_id='11122233344',
        )

    def test_recording_transcript_status_helper_defaults(self):
        event = Event.objects.get(pk=self.event.pk)
        self.assertEqual(
            self._transcript_status(event),
            'none',
        )
        Event.objects.filter(pk=event.pk).update(
            transcript_url='https://zoom.us/rec/download/x.vtt',
        )
        event.refresh_from_db()
        self.assertEqual(self._transcript_status(event), 'waiting')

    def _transcript_status(self, event):
        from events.services.recording_transcript import transcript_status

        return transcript_status(event)
