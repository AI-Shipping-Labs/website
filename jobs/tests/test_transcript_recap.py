"""Transcript ingestion + recap auto-draft pipeline (issue #1597).

Covers:
- VTT parsing (headers, notes, cue numbers, inline tags, rolling-caption
  duplicate collapsing)
- ``transcribe_recording``: download/parse/store, not-ready retry budget,
  terminal unavailable state, recap chaining
- Recap draft gates: LLM disabled, toggle off, existing notes; success
  writes ``recap_notes`` + renders ``recap_notes_html``
- Zoom recordings re-list backfill (``refresh_transcript_from_zoom``)
- Webhook wiring: ``recording.completed`` chains the transcript task;
  ``recording.transcript.completed`` captures late transcripts and clears
  a stale unavailable marker
- The post-S3-upload chain enqueues the transcript job and never fails
  the upload result when that enqueue breaks
"""

from datetime import timedelta
from unittest.mock import MagicMock, patch

import requests
from django.test import TestCase, override_settings
from django.utils import timezone

from events.models import Event
from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting, WebhookLog

SAMPLE_VTT = """WEBVTT

NOTE
This is a note block
that spans lines.

1
00:00:15.240 --> 00:00:18.660
Welcome everyone<v Speaker>, glad you are here</v>

2
00:00:18.660 --> 00:00:21.000
Welcome everyone, glad you are here

3
00:00:21.000 --> 00:00:25.500
Today we cover the <c.colorE5E5E5>recording</c> pipeline.

4
00:00:25.500 --> 00:00:28.000
Second topic: deploying with uv.
"""


def _make_event(**overrides):
    values = {
        'title': 'Transcript Workshop',
        'slug': 'transcript-workshop',
        'start_datetime': timezone.now() - timedelta(hours=3),
        'end_datetime': timezone.now() - timedelta(hours=1),
        'timezone': 'Europe/Berlin',
        'zoom_meeting_id': '12345678901',
        'status': 'completed',
        'required_level': 0,
    }
    values.update(overrides)
    return Event.objects.create(**values)


def _set_setting(key, value):
    IntegrationSetting.objects.update_or_create(
        key=key,
        defaults={'value': value, 'group': 's3_recordings'},
    )
    clear_config_cache()


def _http_error(status_code):
    response = MagicMock()
    response.status_code = status_code
    import requests

    return requests.HTTPError(
        f'{status_code} error', response=response,
    )


class ParseVttToTextTest(TestCase):
    def test_parses_cues_strips_markers_and_collapses_repeats(self):
        from jobs.tasks.recording_transcript import parse_vtt_to_text

        text = parse_vtt_to_text(SAMPLE_VTT)
        lines = text.splitlines()
        self.assertEqual(
            lines,
            [
                'Welcome everyone, glad you are here',
                'Today we cover the recording pipeline.',
                'Second topic: deploying with uv.',
            ],
        )

    def test_header_only_document_parses_to_empty(self):
        from jobs.tasks.recording_transcript import parse_vtt_to_text

        self.assertEqual(parse_vtt_to_text('WEBVTT\n'), '')

    def test_no_metadata_variant(self):
        from jobs.tasks.recording_transcript import parse_vtt_to_text

        raw = (
            'WEBVTT\n'
            '\n'
            '00:00:01.000 --> 00:00:02.000\n'
            'Hello <00:00:01.500>world\n'
        )
        self.assertEqual(parse_vtt_to_text(raw), 'Hello world')

    @patch('jobs.tasks.recording_transcript.requests.get')
    @patch(
        'integrations.services.zoom.get_access_token',
        return_value='test-access-token',
    )
    def test_download_returns_exact_response_bytes(self, mock_token, mock_get):
        from jobs.tasks.recording_transcript import _download_vtt

        raw_vtt = b'WEBVTT\r\n\r\n\xffexact provider bytes\r\n'
        response = MagicMock(content=raw_vtt)
        mock_get.return_value = response

        result = _download_vtt('https://zoom.example/download/raw.vtt')

        self.assertEqual(result, raw_vtt)
        response.raise_for_status.assert_called_once_with()
        mock_get.assert_called_once_with(
            'https://zoom.example/download/raw.vtt?access_token=test-access-token',
            timeout=60,
        )


class TranscribeRecordingTest(TestCase):
    def setUp(self):
        clear_config_cache()
        self.addCleanup(clear_config_cache)
        archive_patcher = patch(
            'jobs.tasks.recording_transcript._archive_vtt',
            return_value=(
                'https://test-recordings-bucket.s3.eu-central-1.amazonaws.com/'
                'recordings/2026/transcript-workshop.vtt'
            ),
        )
        self.archive = archive_patcher.start()
        self.addCleanup(archive_patcher.stop)
        self.event = _make_event(
            transcript_url='https://zoom.us/rec/download/transcript.vtt',
        )

    @patch('integrations.services.llm.is_enabled', return_value=False)
    @patch(
        'jobs.tasks.recording_transcript._download_vtt',
        return_value=SAMPLE_VTT,
    )
    def test_downloads_parses_and_stores_transcript(
        self, mock_download, mock_llm_enabled,
    ):
        from jobs.tasks.recording_transcript import transcribe_recording

        result = transcribe_recording(self.event.id)

        self.assertEqual(result['status'], 'ok')
        self.assertGreater(result['characters'], 0)
        self.event.refresh_from_db()
        self.assertIn('Second topic: deploying with uv.', self.event.transcript_text)
        self.assertEqual(
            self.event.transcript_s3_url,
            'https://test-recordings-bucket.s3.eu-central-1.amazonaws.com/'
            'recordings/2026/transcript-workshop.vtt',
        )
        self.archive.assert_called_once_with(self.event, SAMPLE_VTT)
        self.assertEqual(self.event.transcript_fetch_attempts, 0)
        # LLM disabled: transcript stored, recap skipped with reason.
        self.assertEqual(
            result['recap'],
            {'status': 'skipped', 'reason': 'llm_not_configured'},
        )

    @override_settings()
    @patch('integrations.services.llm.is_enabled', return_value=True)
    @patch('integrations.services.llm.complete')
    @patch('jobs.tasks.helpers.q_async_task')
    @patch(
        'jobs.tasks.recording_transcript._download_vtt',
        return_value=SAMPLE_VTT,
    )
    def test_recap_task_chained_when_llm_and_toggle_on(
        self,
        mock_download,
        mock_q_async,
        mock_complete,
        mock_llm_enabled,
    ):
        from jobs.tasks.recording_transcript import transcribe_recording

        mock_q_async.return_value = 'recap-task-1'
        result = transcribe_recording(self.event.id)

        self.assertEqual(result['recap']['status'], 'queued')
        self.assertEqual(result['recap']['forced'], False)
        queued_funcs = [call[0][0] for call in mock_q_async.call_args_list]
        self.assertIn('jobs.tasks.recap_draft.draft_event_recap', queued_funcs)
        recap_call = next(
            call for call in mock_q_async.call_args_list
            if call[0][0] == 'jobs.tasks.recap_draft.draft_event_recap'
        )
        self.assertEqual(recap_call[0][1], self.event.id)
        self.assertEqual(recap_call[0][2], False)

    @patch('integrations.services.llm.is_enabled', return_value=True)
    @patch('jobs.tasks.helpers.q_async_task')
    @patch(
        'jobs.tasks.recording_transcript._download_vtt',
        return_value=SAMPLE_VTT,
    )
    def test_recap_task_skipped_when_toggle_off(
        self, mock_download, mock_q_async, mock_llm_enabled,
    ):
        from jobs.tasks.recording_transcript import transcribe_recording

        _set_setting('RECORDING_RECAP_AUTO_DRAFT_ENABLED', 'false')
        result = transcribe_recording(self.event.id)

        self.assertEqual(
            result['recap'],
            {'status': 'skipped', 'reason': 'recap_auto_draft_disabled'},
        )
        mock_q_async.assert_not_called()

    @patch('integrations.services.llm.is_enabled', return_value=True)
    @patch(
        'jobs.tasks.recording_transcript._download_vtt',
        return_value=SAMPLE_VTT,
    )
    def test_not_ready_download_counts_attempt_and_raises(
        self, mock_download, mock_llm_enabled,
    ):
        from jobs.tasks.recording_transcript import (
            TranscriptNotReady,
            transcribe_recording,
        )

        mock_download.side_effect = _http_error(404)
        with self.assertRaises(TranscriptNotReady):
            transcribe_recording(self.event.id)
        self.event.refresh_from_db()
        self.assertEqual(self.event.transcript_fetch_attempts, 1)
        self.assertIsNone(self.event.transcript_unavailable_at)

    @patch('integrations.services.llm.is_enabled', return_value=False)
    @patch(
        'jobs.tasks.recording_transcript._download_vtt',
        side_effect=[_http_error(404), SAMPLE_VTT],
    )
    def test_retry_sequence_eventually_succeeds(
        self, mock_download, mock_llm_enabled,
    ):
        from jobs.tasks.recording_transcript import (
            TranscriptNotReady,
            transcribe_recording,
        )

        with self.assertRaises(TranscriptNotReady):
            transcribe_recording(self.event.id)

        result = transcribe_recording(self.event.id)
        self.assertEqual(result['status'], 'ok')
        self.event.refresh_from_db()
        self.assertTrue(self.event.transcript_text)
        self.assertEqual(self.event.transcript_fetch_attempts, 0)
        self.assertIsNone(self.event.transcript_unavailable_at)

    @patch('integrations.services.llm.is_enabled', return_value=False)
    @patch(
        'jobs.tasks.recording_transcript._download_vtt',
        side_effect=requests.Timeout('timed out'),
    )
    def test_network_failure_counts_as_not_ready(
        self, mock_download, mock_llm_enabled,
    ):
        from jobs.tasks.recording_transcript import (
            TranscriptNotReady,
            transcribe_recording,
        )

        with self.assertRaises(TranscriptNotReady):
            transcribe_recording(self.event.id)
        self.event.refresh_from_db()
        self.assertEqual(self.event.transcript_fetch_attempts, 1)

    @patch('integrations.services.llm.is_enabled', return_value=True)
    @patch('jobs.tasks.helpers.q_async_task')
    @patch('jobs.tasks.recording_transcript._download_vtt')
    def test_exhausted_retries_mark_unavailable_and_skip_recap(
        self, mock_download, mock_q_async, mock_llm_enabled,
    ):
        from jobs.tasks.recording_transcript import (
            TRANSCRIPT_MAX_ATTEMPTS,
            transcribe_recording,
        )

        Event.objects.filter(pk=self.event.pk).update(
            transcript_fetch_attempts=TRANSCRIPT_MAX_ATTEMPTS - 1,
        )
        mock_download.side_effect = _http_error(404)

        result = transcribe_recording(self.event.id)

        self.assertEqual(result['status'], 'unavailable')
        self.event.refresh_from_db()
        self.assertIsNotNone(self.event.transcript_unavailable_at)
        self.assertEqual(
            self.event.transcript_fetch_attempts,
            TRANSCRIPT_MAX_ATTEMPTS,
        )
        self.assertFalse(self.event.transcript_text)
        # Terminal state: recap drafting must be skipped, nothing queued.
        mock_q_async.assert_not_called()

    @patch('integrations.services.llm.is_enabled', return_value=False)
    @patch('integrations.services.zoom.get_meeting_recordings')
    def test_no_url_and_zoom_error_counts_attempt(
        self, mock_list, mock_llm_enabled,
    ):
        from integrations.services.zoom import ZoomAPIError
        from jobs.tasks.recording_transcript import (
            TranscriptNotReady,
            transcribe_recording,
        )

        Event.objects.filter(pk=self.event.pk).update(transcript_url='')
        mock_list.side_effect = ZoomAPIError('listing failed')

        with self.assertRaises(TranscriptNotReady):
            transcribe_recording(self.event.id)
        self.event.refresh_from_db()
        self.assertEqual(self.event.transcript_fetch_attempts, 1)

    @patch('integrations.services.llm.is_enabled', return_value=False)
    @patch(
        'jobs.tasks.recording_transcript._download_vtt',
        return_value='WEBVTT\n\n1\n00:00:01.000 --> 00:00:02.000\n\n',
    )
    def test_parse_to_empty_counts_as_not_ready(
        self, mock_download, mock_llm_enabled,
    ):
        from jobs.tasks.recording_transcript import (
            TranscriptNotReady,
            transcribe_recording,
        )

        with self.assertRaises(TranscriptNotReady):
            transcribe_recording(self.event.id)
        self.event.refresh_from_db()
        self.assertEqual(self.event.transcript_fetch_attempts, 1)
        self.assertFalse(self.event.transcript_text)

    @patch('integrations.services.llm.is_enabled', return_value=True)
    @patch('jobs.tasks.helpers.q_async_task')
    def test_redraft_forces_recap_despite_existing_notes(
        self, mock_q_async, mock_llm_enabled,
    ):
        from jobs.tasks.recording_transcript import transcribe_recording

        Event.objects.filter(pk=self.event.pk).update(
            transcript_text='A stored transcript.',
            transcript_s3_url=(
                'https://test-recordings-bucket.s3.eu-central-1.amazonaws.com/'
                'recordings/2026/transcript-workshop.vtt'
            ),
            recap_notes='Operator-authored notes',
        )
        mock_q_async.return_value = 'redraft-task'

        result = transcribe_recording(self.event.id, redraft=True)

        self.assertEqual(result['status'], 'already_stored')
        self.assertEqual(result['recap']['status'], 'queued')
        recap_call = next(
            call for call in mock_q_async.call_args_list
            if call[0][0] == 'jobs.tasks.recap_draft.draft_event_recap'
        )
        self.assertEqual(recap_call[0][2], True)

    @patch('integrations.services.llm.is_enabled', return_value=False)
    @patch(
        'jobs.tasks.recording_transcript._download_vtt',
        return_value=b'WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nArchive me.\n',
    )
    def test_text_only_legacy_row_fills_archive_without_replacing_text(
        self, mock_download, mock_llm_enabled,
    ):
        from jobs.tasks.recording_transcript import transcribe_recording

        Event.objects.filter(pk=self.event.pk).update(
            transcript_text='Keep the already parsed transcript.',
            recap_notes='Operator-authored recap.',
        )

        result = transcribe_recording(self.event.pk)

        self.assertEqual(result['status'], 'ok')
        self.event.refresh_from_db()
        self.assertEqual(
            self.event.transcript_text,
            'Keep the already parsed transcript.',
        )
        self.assertEqual(self.event.recap_notes, 'Operator-authored recap.')
        self.assertTrue(self.event.transcript_s3_url.endswith('.vtt'))
        self.assertEqual(self.archive.call_count, 1)
        archive_event, archive_bytes = self.archive.call_args.args
        self.assertEqual(archive_event.pk, self.event.pk)
        self.assertIn(b'Archive me.', archive_bytes)

    @patch('integrations.services.llm.is_enabled', return_value=False)
    @patch(
        'jobs.tasks.recording_transcript._download_vtt',
        return_value=b'WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nArchive me.\n',
    )
    def test_archive_failure_preserves_existing_text_and_recap(
        self, mock_download, mock_llm_enabled,
    ):
        from jobs.tasks.recording_transcript import (
            TranscriptArchiveError,
            transcribe_recording,
        )

        Event.objects.filter(pk=self.event.pk).update(
            transcript_text='Keep this transcript.',
            recap_notes='Keep this recap.',
        )
        self.archive.side_effect = TranscriptArchiveError('bounded failure')

        with self.assertRaisesMessage(TranscriptArchiveError, 'bounded failure'):
            transcribe_recording(self.event.pk)

        self.event.refresh_from_db()
        self.assertEqual(self.event.transcript_text, 'Keep this transcript.')
        self.assertEqual(self.event.recap_notes, 'Keep this recap.')
        self.assertEqual(self.event.transcript_s3_url, '')
        self.assertIsNone(self.event.transcript_unavailable_at)

    @patch(
        'jobs.tasks.recording_transcript._download_vtt',
        return_value=b'WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nKeep parsed text.\n',
    )
    def test_archive_failure_keeps_newly_parsed_text_for_retry(self, mock_download):
        from jobs.tasks.recording_transcript import (
            TranscriptArchiveError,
            transcribe_recording,
        )

        self.archive.side_effect = TranscriptArchiveError('bounded failure')

        with self.assertRaisesMessage(TranscriptArchiveError, 'bounded failure'):
            transcribe_recording(self.event.pk)

        self.event.refresh_from_db()
        self.assertEqual(self.event.transcript_text, 'Keep parsed text.')
        self.assertEqual(self.event.transcript_s3_url, '')
        self.assertIsNone(self.event.transcript_unavailable_at)

    @patch(
        'jobs.tasks.recording_transcript._download_vtt',
        return_value=b'WEBVTT\n\n',
    )
    def test_text_only_row_rejects_unusable_archive_source(self, mock_download):
        from jobs.tasks.recording_transcript import (
            TranscriptArchiveNotReady,
            transcribe_recording,
        )

        Event.objects.filter(pk=self.event.pk).update(
            transcript_text='Keep this transcript.',
            recap_notes='Keep this recap.',
        )

        with self.assertRaisesMessage(
            TranscriptArchiveNotReady,
            'VTT archive source not ready',
        ):
            transcribe_recording(self.event.pk)

        self.event.refresh_from_db()
        self.assertEqual(self.event.transcript_text, 'Keep this transcript.')
        self.assertEqual(self.event.recap_notes, 'Keep this recap.')
        self.assertEqual(self.event.transcript_s3_url, '')
        self.assertIsNone(self.event.transcript_unavailable_at)
        self.archive.assert_not_called()

    @patch('jobs.tasks.recording_transcript._download_vtt')
    def test_complete_row_skips_zoom_and_s3(self, mock_download):
        from jobs.tasks.recording_transcript import transcribe_recording

        Event.objects.filter(pk=self.event.pk).update(
            transcript_text='Already complete.',
            transcript_s3_url=(
                'https://test-recordings-bucket.s3.eu-central-1.amazonaws.com/'
                'recordings/2026/transcript-workshop.vtt'
            ),
        )

        result = transcribe_recording(self.event.pk)

        self.assertEqual(result['status'], 'already_stored')
        mock_download.assert_not_called()
        self.archive.assert_not_called()


class RecapDraftTaskTest(TestCase):
    def setUp(self):
        clear_config_cache()
        self.addCleanup(clear_config_cache)
        self.event = _make_event(transcript_text='A rich transcript.')

    @patch('integrations.services.llm.is_enabled', return_value=True)
    @patch('integrations.services.llm.complete')
    def test_draft_writes_notes_and_renders_html(
        self, mock_complete, mock_llm_enabled,
    ):
        from jobs.tasks.recap_draft import draft_event_recap

        mock_complete.return_value = MagicMock(
            text='## What we covered\n\n- The pipeline.',
        )
        result = draft_event_recap(self.event.id)

        self.assertEqual(result['status'], 'drafted')
        self.event.refresh_from_db()
        self.assertIn('What we covered', self.event.recap_notes)
        self.assertIn('What we covered', self.event.recap_notes_html)

    @patch('integrations.services.llm.is_enabled', return_value=True)
    @patch('integrations.services.llm.complete')
    def test_automatic_draft_never_overwrites_existing_notes(
        self, mock_complete, mock_llm_enabled,
    ):
        from jobs.tasks.recap_draft import draft_event_recap

        Event.objects.filter(pk=self.event.pk).update(
            recap_notes='Operator notes win',
        )
        result = draft_event_recap(self.event.id)

        self.assertEqual(result['status'], 'skipped')
        self.assertEqual(result['reason'], 'recap_notes_exist')
        self.event.refresh_from_db()
        self.assertEqual(self.event.recap_notes, 'Operator notes win')
        mock_complete.assert_not_called()

    @patch('integrations.services.llm.is_enabled', return_value=True)
    @patch('integrations.services.llm.complete')
    def test_automatic_draft_preserves_notes_saved_during_llm_call(
        self, mock_complete, mock_llm_enabled,
    ):
        from jobs.tasks.recap_draft import draft_event_recap

        def save_operator_notes(*args, **kwargs):
            Event.objects.filter(pk=self.event.pk).update(
                recap_notes='Operator notes saved while the LLM ran',
            )
            return MagicMock(text='## Generated recap')

        mock_complete.side_effect = save_operator_notes
        result = draft_event_recap(self.event.id)

        self.assertEqual(result['status'], 'skipped')
        self.assertEqual(result['reason'], 'recap_notes_exist')
        self.event.refresh_from_db()
        self.assertEqual(
            self.event.recap_notes,
            'Operator notes saved while the LLM ran',
        )

    @patch('integrations.services.llm.is_enabled', return_value=True)
    @patch('integrations.services.llm.complete')
    def test_redraft_overwrites_existing_notes(
        self, mock_complete, mock_llm_enabled,
    ):
        from jobs.tasks.recap_draft import draft_event_recap

        Event.objects.filter(pk=self.event.pk).update(
            recap_notes='Stale draft',
        )
        mock_complete.return_value = MagicMock(text='## Fresh recap')
        result = draft_event_recap(self.event.id, force=True)

        self.assertEqual(result['status'], 'drafted')
        self.event.refresh_from_db()
        self.assertEqual(self.event.recap_notes, '## Fresh recap')

    @patch('integrations.services.llm.is_enabled', return_value=True)
    @patch('integrations.services.llm.complete')
    def test_missing_transcript_skips(
        self, mock_complete, mock_llm_enabled,
    ):
        from jobs.tasks.recap_draft import draft_event_recap

        Event.objects.filter(pk=self.event.pk).update(transcript_text='')
        result = draft_event_recap(self.event.id)

        self.assertEqual(
            result,
            {
                'status': 'skipped',
                'reason': 'missing_transcript',
                'event_id': self.event.id,
            },
        )
        mock_complete.assert_not_called()

    @patch('integrations.services.llm.is_enabled', return_value=False)
    @patch('integrations.services.llm.complete')
    def test_llm_disabled_skips(
        self, mock_complete, mock_llm_enabled,
    ):
        from jobs.tasks.recap_draft import draft_event_recap

        result = draft_event_recap(self.event.id)
        self.assertEqual(
            result,
            {
                'status': 'skipped',
                'reason': 'llm_not_configured',
                'event_id': self.event.id,
            },
        )
        mock_complete.assert_not_called()

    @patch('integrations.services.llm.is_enabled', return_value=True)
    @patch('integrations.services.llm.complete')
    def test_llm_error_returns_error_without_raising(
        self, mock_complete, mock_llm_enabled,
    ):
        from integrations.services.llm import LLMError
        from jobs.tasks.recap_draft import draft_event_recap

        mock_complete.side_effect = LLMError('provider down')
        result = draft_event_recap(self.event.id)

        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['reason'], 'llm_error')
        self.event.refresh_from_db()
        self.assertEqual(self.event.recap_notes, '')


class RefreshTranscriptFromZoomTest(TestCase):
    def setUp(self):
        clear_config_cache()
        self.addCleanup(clear_config_cache)
        self.event = _make_event()

    @patch('jobs.tasks.helpers.q_async_task')
    @patch('integrations.services.zoom.get_meeting_recordings')
    @patch('integrations.services.zoom.requests.post')
    def test_backfills_transcript_and_download_urls(
        self, mock_zoom_post, mock_list, mock_q_async,
    ):
        from events.services.recording_transcript import (
            refresh_transcript_from_zoom,
        )

        mock_q_async.return_value = 'task-1'
        token_response = MagicMock()
        token_response.status_code = 200
        token_response.json.return_value = {
            'access_token': 't', 'expires_in': 3600,
        }
        mock_zoom_post.return_value = token_response
        mock_list.return_value = {
            'recording_files': [
                {
                    'recording_type': 'audio_transcript',
                    'download_url': 'https://zoom.us/rec/download/vtt',
                },
                {
                    'recording_type': 'shared_screen',
                    'play_url': 'https://zoom.us/rec/play/v',
                    'download_url': 'https://zoom.us/rec/download/mp4',
                },
            ],
        }

        refreshed = refresh_transcript_from_zoom(self.event)

        self.assertTrue(refreshed['transcript_url'])
        self.assertTrue(refreshed['recording_download_url'])
        self.assertTrue(refreshed['upload_queued'])
        self.event.refresh_from_db()
        self.assertEqual(
            self.event.transcript_url,
            'https://zoom.us/rec/download/vtt',
        )
        self.assertEqual(
            self.event.recording_zoom_download_url,
            'https://zoom.us/rec/download/mp4',
        )
        queued_funcs = [call[0][0] for call in mock_q_async.call_args_list]
        self.assertIn(
            'jobs.tasks.recording_upload.upload_recording_to_s3',
            queued_funcs,
        )

    def test_event_without_zoom_meeting_is_skipped(self):
        from events.services.recording_transcript import (
            refresh_transcript_from_zoom,
        )

        event = _make_event(zoom_meeting_id='', slug='no-zoom-event')
        refreshed = refresh_transcript_from_zoom(event)
        self.assertEqual(refreshed, {'refreshed': False, 'reason': 'no_meeting_id'})


@override_settings(ZOOM_WEBHOOK_SECRET_TOKEN='test-zoom-webhook-secret')
class TranscriptWebhookTest(TestCase):
    def setUp(self):
        clear_config_cache()
        self.addCleanup(clear_config_cache)
        self.event = _make_event()

    def _webhook_log(self, event_type):
        return WebhookLog.objects.create(
            service='zoom', event_type=event_type, payload={}, processed=False,
        )

    @patch('jobs.tasks.helpers.q_async_task')
    def test_recording_completed_enqueues_transcript_task(self, mock_q_async):
        from integrations.views.zoom_webhook import _handle_recording_completed

        mock_q_async.return_value = 'task-1'
        payload = {
            'event': 'recording.completed',
            'payload': {
                'object': {
                    'id': self.event.zoom_meeting_id,
                    'recording_files': [
                        {
                            'recording_type': 'shared_screen',
                            'play_url': 'https://zoom.us/rec/play/v',
                            'download_url': 'https://zoom.us/rec/download/mp4',
                        },
                        {
                            'recording_type': 'audio_transcript',
                            'download_url': 'https://zoom.us/rec/download/vtt',
                        },
                    ],
                },
            },
        }

        with patch(
            'events.services.recording_upload.enqueue_recording_upload_task',
        ):
            _handle_recording_completed(
                payload, self._webhook_log('recording.completed'),
            )

        queued_funcs = [call[0][0] for call in mock_q_async.call_args_list]
        self.assertIn(
            'jobs.tasks.recording_transcript.transcribe_recording',
            queued_funcs,
        )
        self.event.refresh_from_db()
        self.assertEqual(
            self.event.transcript_url,
            'https://zoom.us/rec/download/vtt',
        )

    @patch('jobs.tasks.helpers.q_async_task')
    def test_recording_completed_skips_transcript_when_toggle_off(
        self, mock_q_async,
    ):
        from integrations.views.zoom_webhook import _handle_recording_completed

        _set_setting('RECORDING_TRANSCRIPT_INGEST_ENABLED', 'false')
        payload = {
            'event': 'recording.completed',
            'payload': {
                'object': {
                    'id': self.event.zoom_meeting_id,
                    'recording_files': [
                        {
                            'recording_type': 'audio_transcript',
                            'download_url': 'https://zoom.us/rec/download/vtt',
                        },
                    ],
                },
            },
        }

        with patch(
            'events.services.recording_upload.enqueue_recording_upload_task',
        ):
            _handle_recording_completed(
                payload, self._webhook_log('recording.completed'),
            )

        queued_funcs = [call[0][0] for call in mock_q_async.call_args_list]
        self.assertNotIn(
            'jobs.tasks.recording_transcript.transcribe_recording',
            queued_funcs,
        )
        # The webhook still stores the URL for a later manual sync.
        self.event.refresh_from_db()
        self.assertEqual(
            self.event.transcript_url,
            'https://zoom.us/rec/download/vtt',
        )

    @patch('jobs.tasks.helpers.q_async_task')
    def test_transcript_webhook_captures_late_transcript(self, mock_q_async):
        from integrations.views.zoom_webhook import (
            _handle_recording_transcript_completed,
        )

        Event.objects.filter(pk=self.event.pk).update(
            recording_s3_url='https://bucket.s3.eu-central-1.amazonaws.com/rec.mp4',
        )
        mock_q_async.return_value = 'task-1'
        payload = {
            'event': 'recording.transcript.completed',
            'payload': {
                'object': {
                    'id': self.event.zoom_meeting_id,
                    'recording_files': [
                        {
                            'recording_type': 'audio_transcript',
                            'download_url': 'https://zoom.us/rec/download/late.vtt',
                        },
                    ],
                },
            },
        }

        _handle_recording_transcript_completed(
            payload, self._webhook_log('recording.transcript.completed'),
        )

        self.event.refresh_from_db()
        self.assertEqual(
            self.event.transcript_url,
            'https://zoom.us/rec/download/late.vtt',
        )
        queued_funcs = [call[0][0] for call in mock_q_async.call_args_list]
        self.assertIn(
            'jobs.tasks.recording_transcript.transcribe_recording',
            queued_funcs,
        )

    @patch('jobs.tasks.helpers.q_async_task')
    def test_transcript_webhook_clears_unavailable_marker(self, mock_q_async):
        from integrations.views.zoom_webhook import (
            _handle_recording_transcript_completed,
        )

        Event.objects.filter(pk=self.event.pk).update(
            transcript_unavailable_at=timezone.now(),
            transcript_fetch_attempts=4,
        )
        mock_q_async.return_value = 'task-1'
        payload = {
            'event': 'recording.transcript.completed',
            'payload': {
                'object': {
                    'id': self.event.zoom_meeting_id,
                    'recording_files': [
                        {
                            'recording_type': 'audio_transcript',
                            'download_url': 'https://zoom.us/rec/download/late.vtt',
                        },
                    ],
                },
            },
        }

        _handle_recording_transcript_completed(
            payload, self._webhook_log('recording.transcript.completed'),
        )

        self.event.refresh_from_db()
        self.assertIsNone(self.event.transcript_unavailable_at)
        self.assertEqual(self.event.transcript_fetch_attempts, 0)
        queued_funcs = [call[0][0] for call in mock_q_async.call_args_list]
        self.assertIn(
            'jobs.tasks.recording_transcript.transcribe_recording',
            queued_funcs,
        )


@override_settings(
    AWS_S3_RECORDINGS_BUCKET='test-recordings-bucket',
    AWS_S3_RECORDINGS_REGION='eu-central-1',
    AWS_ACCESS_KEY_ID='test-key-id',
    AWS_SECRET_ACCESS_KEY='test-secret-key',
    ZOOM_CLIENT_ID='test-client-id',
    ZOOM_CLIENT_SECRET='test-client-secret',
    ZOOM_ACCOUNT_ID='test-account-id',
)
class UploadChainTranscriptEnqueueTest(TestCase):
    """The S3 upload chains the transcript job, and only that can fail."""

    def setUp(self):
        from integrations.services import zoom

        zoom.clear_token_cache()
        clear_config_cache()
        self.addCleanup(clear_config_cache)
        self.event = _make_event(
            transcript_url='https://zoom.us/rec/download/vtt',
        )

    def _upload_mocks(self, mock_zoom_post, mock_requests_get, mock_boto):
        token_response = MagicMock()
        token_response.status_code = 200
        token_response.json.return_value = {
            'access_token': 'test-token', 'expires_in': 3600,
        }
        mock_zoom_post.return_value = token_response

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.iter_content.return_value = [b'fake-video-data']
        mock_response.raise_for_status = MagicMock()
        mock_requests_get.return_value = mock_response

        mock_s3 = MagicMock()
        mock_boto.return_value = mock_s3

    @patch('jobs.tasks.helpers.q_async_task')
    @patch('jobs.tasks.recordings_s3.boto3.client')
    @patch('jobs.tasks.recording_upload.requests.get')
    @patch('integrations.services.zoom.requests.post')
    def test_successful_upload_enqueues_transcript_job(
        self, mock_zoom_post, mock_requests_get, mock_boto, mock_q_async,
    ):
        from jobs.tasks.recording_upload import upload_recording_to_s3

        mock_q_async.return_value = 'transcript-task'
        self._upload_mocks(mock_zoom_post, mock_requests_get, mock_boto)

        result = upload_recording_to_s3(
            self.event.id, 'https://zoom.us/rec/download/mp4',
        )

        self.assertEqual(result['status'], 'ok', result)
        queued_funcs = [call[0][0] for call in mock_q_async.call_args_list]
        self.assertIn(
            'jobs.tasks.recording_transcript.transcribe_recording',
            queued_funcs,
        )

    @patch('jobs.tasks.helpers.q_async_task')
    @patch('jobs.tasks.recordings_s3.boto3.client')
    @patch('jobs.tasks.recording_upload.requests.get')
    @patch('integrations.services.zoom.requests.post')
    def test_transcript_enqueue_failure_never_fails_upload(
        self, mock_zoom_post, mock_requests_get, mock_boto, mock_q_async,
    ):
        from jobs.tasks.recording_upload import upload_recording_to_s3

        self._upload_mocks(mock_zoom_post, mock_requests_get, mock_boto)
        mock_q_async.side_effect = RuntimeError('queue down')

        result = upload_recording_to_s3(
            self.event.id, 'https://zoom.us/rec/download/mp4',
        )

        self.assertEqual(result['status'], 'ok', result)
        self.event.refresh_from_db()
        self.assertTrue(self.event.recording_s3_url)

    @patch('jobs.tasks.helpers.q_async_task')
    @patch('jobs.tasks.recordings_s3.boto3.client')
    @patch('jobs.tasks.recording_upload.requests.get')
    @patch('integrations.services.zoom.requests.post')
    def test_upload_skips_transcript_job_when_toggle_off(
        self, mock_zoom_post, mock_requests_get, mock_boto, mock_q_async,
    ):
        from jobs.tasks.recording_upload import upload_recording_to_s3

        _set_setting('RECORDING_TRANSCRIPT_INGEST_ENABLED', 'false')
        self._upload_mocks(mock_zoom_post, mock_requests_get, mock_boto)

        result = upload_recording_to_s3(
            self.event.id, 'https://zoom.us/rec/download/mp4',
        )

        self.assertEqual(result['status'], 'ok', result)
        queued_funcs = [call[0][0] for call in mock_q_async.call_args_list]
        self.assertNotIn(
            'jobs.tasks.recording_transcript.transcribe_recording',
            queued_funcs,
        )
