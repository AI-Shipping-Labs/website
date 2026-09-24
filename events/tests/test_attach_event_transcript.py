"""Operator command for attaching a transcript VTT to an event."""

import os
import tempfile
from datetime import timedelta
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from events.models import Event
from integrations.config import clear_config_cache

SAMPLE_VTT = """WEBVTT

00:00:01.000 --> 00:00:02.000
Hello from the session.
"""


class AttachEventTranscriptCommandTest(TestCase):
    def setUp(self):
        clear_config_cache()
        self.addCleanup(clear_config_cache)
        now = timezone.now()
        self.event = Event.objects.create(
            title='Attach command event',
            slug='attach-command-event',
            start_datetime=now - timedelta(hours=3),
            end_datetime=now - timedelta(hours=1),
            status='completed',
        )

    def _vtt_file(self, content=SAMPLE_VTT):
        handle = tempfile.NamedTemporaryFile(
            mode='w', suffix='.vtt', delete=False, encoding='utf-8',
        )
        handle.write(content)
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        return handle.name

    def test_dry_run_parses_without_writing(self):
        call_command(
            'attach_event_transcript',
            slug=self.event.slug,
            vtt=self._vtt_file(),
        )

        self.event.refresh_from_db()
        self.assertEqual(self.event.transcript_text, '')
        self.assertEqual(self.event.transcript_s3_url, '')

    def test_dry_run_rejects_empty_parse(self):
        with self.assertRaisesMessage(Exception, 'empty text'):
            call_command(
                'attach_event_transcript',
                slug=self.event.slug,
                vtt=self._vtt_file('WEBVTT\n'),
            )

    @patch(
        'jobs.tasks.recording_transcript._archive_transcript_text',
        return_value='https://bucket.example/recordings/2026/attach-command-event.txt',
    )
    @patch(
        'jobs.tasks.recording_transcript._archive_vtt',
        return_value='https://bucket.example/recordings/2026/attach-command-event.vtt',
    )
    def test_commit_stores_text_and_archives(self, mock_vtt, mock_text):
        call_command(
            'attach_event_transcript',
            slug=self.event.slug,
            vtt=self._vtt_file('\ufeff' + SAMPLE_VTT),
            commit=True,
        )

        self.event.refresh_from_db()
        self.assertIn('Hello from the session.', self.event.transcript_text)
        self.assertTrue(self.event.transcript_s3_url.endswith('.vtt'))
        mock_vtt.assert_called_once_with(
            self.event,
            ('\ufeff' + SAMPLE_VTT).encode('utf-8'),
        )
        mock_text.assert_called_once()

    def test_unknown_slug_errors(self):
        with self.assertRaisesMessage(Exception, 'No event with slug'):
            call_command(
                'attach_event_transcript',
                slug='missing-event',
                vtt=self._vtt_file(),
            )
