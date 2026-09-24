"""API contract for attaching an operator-supplied transcript VTT."""

import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import Token
from events.models import Event
from integrations.config import clear_config_cache

User = get_user_model()

SAMPLE_VTT = """WEBVTT

00:00:01.000 --> 00:00:02.000
Hello from the session.

00:00:03.000 --> 00:00:04.000
Second topic: deploying with uv.
"""


def _past_start_end():
    now = timezone.now()
    return now - timedelta(hours=3), now - timedelta(hours=1)


class EventAttachTranscriptApiTest(TestCase):
    def setUp(self):
        clear_config_cache()
        self.addCleanup(clear_config_cache)
        self.staff = User.objects.create_user(
            email='attach-api-staff@example.com',
            password='pw',
            is_staff=True,
        )
        self.staff_token = Token.objects.create(user=self.staff, name='attach')
        start, end = _past_start_end()
        self.event = Event.objects.create(
            title='Attach API event',
            slug='attach-api-event',
            start_datetime=start,
            end_datetime=end,
            status='completed',
        )
        archive_patcher = patch(
            'jobs.tasks.recording_transcript._archive_vtt',
            return_value=(
                'https://test-recordings-bucket.s3.eu-central-1.amazonaws.com/'
                'recordings/2026/attach-api-event.vtt'
            ),
        )
        self.archive_vtt = archive_patcher.start()
        self.addCleanup(archive_patcher.stop)
        text_patcher = patch(
            'jobs.tasks.recording_transcript._archive_transcript_text',
            return_value=(
                'https://test-recordings-bucket.s3.eu-central-1.amazonaws.com/'
                'recordings/2026/attach-api-event.txt'
            ),
        )
        self.archive_text = text_patcher.start()
        self.addCleanup(text_patcher.stop)

    def _auth(self, key=None):
        token = self.staff_token.key if key is None else key
        return {'HTTP_AUTHORIZATION': f'Token {token}'}

    def _post(self, payload, **kwargs):
        return self.client.post(
            f'/api/events/{self.event.slug}/attach-transcript',
            data=json.dumps(payload),
            content_type='application/json',
            **kwargs,
        )

    def test_attach_without_token_is_401(self):
        response = self.client.post(
            f'/api/events/{self.event.slug}/attach-transcript',
            data=json.dumps({'vtt': SAMPLE_VTT}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 401)
        self.archive_vtt.assert_not_called()

    def test_attach_unknown_slug_is_404(self):
        response = self.client.post(
            '/api/events/missing-attach/attach-transcript',
            data=json.dumps({'vtt': SAMPLE_VTT}),
            content_type='application/json',
            **self._auth(),
        )
        self.assertEqual(response.status_code, 404)

    def test_attach_malformed_body_is_400(self):
        response = self.client.post(
            f'/api/events/{self.event.slug}/attach-transcript',
            data='{not json',
            content_type='application/json',
            **self._auth(),
        )
        self.assertEqual(response.status_code, 400)

    def test_attach_missing_vtt_is_422(self):
        response = self._post({}, **self._auth())
        self.assertEqual(response.status_code, 422)
        self.archive_vtt.assert_not_called()

    def test_attach_empty_parse_is_422(self):
        response = self._post({'vtt': 'WEBVTT\n'}, **self._auth())
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()['code'], 'empty_transcript')
        self.archive_vtt.assert_not_called()

    def test_attach_stores_text_and_both_archives(self):
        Event.objects.filter(pk=self.event.pk).update(
            transcript_unavailable_at=timezone.now(),
            transcript_fetch_attempts=4,
        )

        response = self._post({'vtt': SAMPLE_VTT}, **self._auth())

        self.assertContains(response, 'stored')
        body = response.json()
        self.assertEqual(body['transcript_status'], 'stored')
        self.assertGreater(body['characters'], 0)
        self.assertTrue(body['transcript_s3_url'].endswith('.vtt'))
        self.assertTrue(body['transcript_txt_s3_url'].endswith('.txt'))
        self.assertFalse(body['recap_queued'])
        self.event.refresh_from_db()
        self.assertIn('Hello from the session.', self.event.transcript_text)
        self.assertTrue(self.event.transcript_s3_url.endswith('.vtt'))
        self.assertIsNone(self.event.transcript_unavailable_at)
        self.assertEqual(self.event.transcript_fetch_attempts, 0)
        self.archive_vtt.assert_called_once_with(self.event, SAMPLE_VTT)
        self.archive_text.assert_called_once_with(
            self.event,
            'Hello from the session.\nSecond topic: deploying with uv.',
        )

    def test_attach_archive_failure_is_422_without_storing(self):
        from jobs.tasks.recording_transcript import TranscriptArchiveError

        self.archive_vtt.side_effect = TranscriptArchiveError('bounded failure')

        response = self._post({'vtt': SAMPLE_VTT}, **self._auth())

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()['code'], 'transcript_archive_failed')
        self.event.refresh_from_db()
        self.assertEqual(self.event.transcript_text, '')
        self.assertEqual(self.event.transcript_s3_url, '')

    @patch('api.views.events.llm_is_enabled', return_value=False)
    def test_attach_redraft_without_llm_is_422(self, _mock_llm):
        response = self._post(
            {'vtt': SAMPLE_VTT, 'redraft': True}, **self._auth(),
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()['code'], 'llm_not_configured')
        self.archive_vtt.assert_not_called()
