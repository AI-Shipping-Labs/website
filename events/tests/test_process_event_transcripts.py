"""Operator recovery command for transcript text and raw VTT archives."""

from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from events.models import Event
from integrations.services.zoom import ZoomAPIError


def _event(slug, **overrides):
    values = {
        'title': slug,
        'slug': slug,
        'platform': 'zoom',
        'zoom_meeting_id': f'meeting-{slug}',
        'start_datetime': timezone.now() - timedelta(hours=3),
        'end_datetime': timezone.now() - timedelta(hours=2),
        'status': 'completed',
    }
    values.update(overrides)
    return Event.objects.create(**values)


class ProcessEventTranscriptsCommandTest(TestCase):
    @patch(
        'events.management.commands.process_event_transcripts.'
        'enqueue_recording_transcript_task'
    )
    @patch(
        'events.management.commands.process_event_transcripts.'
        'claim_and_enqueue_recording_upload'
    )
    @patch(
        'events.management.commands.process_event_transcripts.'
        'refresh_transcript_from_zoom'
    )
    def test_dry_run_selects_only_eligible_incomplete_events_in_order(
        self, mock_refresh, mock_claim, mock_transcript,
    ):
        earliest_start = timezone.now() - timedelta(days=2)
        earliest_first = _event(
            'earliest-first',
            start_datetime=earliest_start,
            transcript_text='Text exists but archive does not.',
        )
        earliest_second = _event(
            'earliest-second',
            start_datetime=earliest_start,
        )
        later = _event(
            'later-incomplete',
            start_datetime=timezone.now() - timedelta(days=1),
        )
        _event(
            'complete',
            transcript_text='Complete text.',
            transcript_s3_url='https://bucket.example/complete.vtt',
        )
        _event('cancelled', status='cancelled')
        _event(
            'future',
            status='upcoming',
            start_datetime=timezone.now() + timedelta(days=1),
            end_datetime=timezone.now() + timedelta(days=1, hours=1),
        )
        _event('custom', platform='custom')
        _event('no-meeting', zoom_meeting_id='')
        stdout = StringIO()

        call_command('process_event_transcripts', all_missing=True, stdout=stdout)

        output = stdout.getvalue()
        self.assertIn('selected=3', output)
        self.assertIn('would_process=3', output)
        self.assertLess(
            output.index(earliest_first.slug),
            output.index(earliest_second.slug),
        )
        self.assertLess(output.index(earliest_second.slug), output.index(later.slug))
        for excluded in ('complete', 'cancelled', 'future', 'custom', 'no-meeting'):
            self.assertNotIn(f'slug={excluded}', output)
        mock_refresh.assert_not_called()
        mock_claim.assert_not_called()
        mock_transcript.assert_not_called()

    def test_requires_exactly_one_selector(self):
        with self.assertRaises(CommandError):
            call_command('process_event_transcripts')
        with self.assertRaises(CommandError):
            call_command(
                'process_event_transcripts',
                slug='one',
                all_missing=True,
            )

    @patch(
        'events.management.commands.process_event_transcripts.'
        'enqueue_recording_transcript_task',
        return_value='transcript-task',
    )
    @patch(
        'events.management.commands.process_event_transcripts.'
        'claim_and_enqueue_recording_upload'
    )
    @patch(
        'events.management.commands.process_event_transcripts.'
        'refresh_transcript_from_zoom',
        return_value={'refreshed': True},
    )
    def test_commit_reuses_recording_claim_and_transcript_enqueue(
        self, mock_refresh, mock_claim, mock_transcript,
    ):
        event = _event(
            'commit-incomplete',
            recording_zoom_download_url='https://zoom.example/download/mp4',
        )
        mock_claim.return_value = (event, 'queued')
        stdout = StringIO()

        call_command(
            'process_event_transcripts',
            slug=event.slug,
            commit=True,
            stdout=stdout,
        )

        mock_refresh.assert_called_once_with(event)
        mock_claim.assert_called_once_with(
            event.pk,
            source='Transcript backfill command',
        )
        queued_event = mock_transcript.call_args.args[0]
        self.assertEqual(queued_event.pk, event.pk)
        self.assertEqual(
            mock_transcript.call_args.kwargs['source'],
            'Transcript backfill command',
        )
        self.assertIn('recording_queued=1', stdout.getvalue())
        self.assertIn('transcript_queued=1', stdout.getvalue())

    @patch(
        'events.management.commands.process_event_transcripts.'
        'enqueue_recording_transcript_task',
        return_value='transcript-task',
    )
    @patch(
        'events.management.commands.process_event_transcripts.'
        'claim_and_enqueue_recording_upload',
        return_value=(None, 'in_progress'),
    )
    @patch(
        'events.management.commands.process_event_transcripts.'
        'refresh_transcript_from_zoom',
        return_value={'refreshed': True, 'upload_queued': True},
    )
    def test_commit_counts_recording_enqueued_during_zoom_refresh(
        self, mock_refresh, mock_claim, mock_transcript,
    ):
        event = _event('refresh-enqueues-recording')
        stdout = StringIO()

        call_command(
            'process_event_transcripts',
            slug=event.slug,
            commit=True,
            stdout=stdout,
        )

        self.assertEqual(mock_refresh.call_count, 1)
        self.assertEqual(mock_claim.call_count, 1)
        self.assertEqual(mock_transcript.call_count, 1)
        output = stdout.getvalue()
        self.assertIn('recording_queued=1', output)
        self.assertIn('transcript_queued=1', output)

    @patch(
        'events.management.commands.process_event_transcripts.'
        'enqueue_recording_transcript_task'
    )
    @patch(
        'events.management.commands.process_event_transcripts.'
        'claim_and_enqueue_recording_upload'
    )
    @patch(
        'events.management.commands.process_event_transcripts.'
        'refresh_transcript_from_zoom'
    )
    def test_complete_explicit_event_is_a_noop(
        self, mock_refresh, mock_claim, mock_transcript,
    ):
        event = _event(
            'already-complete',
            transcript_text='Stored text.',
            transcript_s3_url='https://bucket.example/already-complete.vtt',
        )
        stdout = StringIO()

        call_command(
            'process_event_transcripts',
            slug=event.slug,
            commit=True,
            stdout=stdout,
        )

        self.assertIn('outcome=complete', stdout.getvalue())
        mock_refresh.assert_not_called()
        mock_claim.assert_not_called()
        mock_transcript.assert_not_called()

    @patch(
        'events.management.commands.process_event_transcripts.'
        'enqueue_recording_transcript_task',
        return_value='transcript-task',
    )
    @patch(
        'events.management.commands.process_event_transcripts.'
        'claim_and_enqueue_recording_upload'
    )
    @patch(
        'events.management.commands.process_event_transcripts.'
        'refresh_transcript_from_zoom'
    )
    def test_failure_is_bounded_nonzero_and_does_not_stop_later_candidate(
        self, mock_refresh, mock_claim, mock_transcript,
    ):
        first = _event(
            'safe-failure',
            start_datetime=timezone.now() - timedelta(days=2),
        )
        second = _event(
            'safe-success',
            start_datetime=timezone.now() - timedelta(days=1),
            recording_s3_url='https://bucket.example/safe-success.mp4',
        )
        secret = 'PII_TOKEN_zoom_download_url'
        mock_refresh.side_effect = [ZoomAPIError(secret), {'refreshed': True}]
        stdout = StringIO()
        stderr = StringIO()

        with self.assertRaisesMessage(CommandError, '1 failed event'):
            call_command(
                'process_event_transcripts',
                all_missing=True,
                commit=True,
                stdout=stdout,
                stderr=stderr,
            )

        combined = stdout.getvalue() + stderr.getvalue()
        self.assertIn('outcome=zoom_refresh_failed', combined)
        self.assertIn('failed=1', combined)
        self.assertIn('transcript_queued=1', combined)
        self.assertNotIn(secret, combined)
        self.assertEqual(mock_refresh.call_count, 2)
        self.assertEqual(mock_refresh.call_args_list[0].args[0].pk, first.pk)
        self.assertEqual(mock_refresh.call_args_list[1].args[0].pk, second.pk)
        mock_claim.assert_not_called()
        self.assertEqual(mock_transcript.call_count, 1)
        self.assertEqual(mock_transcript.call_args.args[0].pk, second.pk)

    def test_rejects_unknown_nonzoom_and_not_ended_events(self):
        with self.assertRaisesMessage(CommandError, 'No event with slug=missing'):
            call_command('process_event_transcripts', slug='missing')

        custom = _event('custom-explicit', platform='custom')
        with self.assertRaisesMessage(CommandError, 'not a Zoom event'):
            call_command('process_event_transcripts', slug=custom.slug)

        future = _event(
            'future-explicit',
            status='upcoming',
            start_datetime=timezone.now() + timedelta(days=1),
            end_datetime=timezone.now() + timedelta(days=1, hours=1),
        )
        with self.assertRaisesMessage(CommandError, 'has not ended'):
            call_command('process_event_transcripts', slug=future.slug)
