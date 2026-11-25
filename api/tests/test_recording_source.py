"""Authenticated timestamped transcript source and safe preparation progress."""

from io import BytesIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import Token
from events.tests.test_recording_pipeline_watch import make_event


class RecordingSourceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user = get_user_model().objects.create_user(email='source-staff@example.com', is_staff=True)
        cls.token = Token.objects.create(user=user, name='recording-source-test')

    def setUp(self):
        self.event = make_event()
        self.url = f'/api/events/{self.event.slug}/preparation-source'
        self.headers = {'HTTP_AUTHORIZATION': f'Token {self.token.key}'}

    def test_source_requires_staff_token(self):
        self.assertEqual(self.client.get(self.url).status_code, 401)

    def test_source_retries_until_transcript_is_durably_captured(self):
        response = self.client.get(self.url, **self.headers)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['code'], 'transcript_not_ready')

    @patch('api.views.recording_source.get_recordings_s3_client')
    def test_source_preserves_timing_without_exposing_storage_url(self, client):
        vtt = 'WEBVTT\n\n00:00:10.000 --> 00:00:15.000\nA useful topic.\n'
        client.return_value.get_object.return_value = {'Body': BytesIO(vtt.encode())}
        self.event.transcript_text = 'A useful topic.'
        self.event.transcript_s3_url = 'https://private.example.com/secret.vtt'
        self.event.save()
        response = self.client.get(self.url, **self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['transcript_vtt'], vtt)
        self.assertNotIn('private.example.com', response.content.decode())

    @patch('api.views.recording_source.get_recordings_s3_client')
    def test_oversized_source_is_rejected_instead_of_silent_truncation(self, client):
        client.return_value.get_object.return_value = {'Body': BytesIO(b'x' * (2 * 1024 * 1024 + 1))}
        self.event.transcript_text = 'Stored text'
        self.event.transcript_s3_url = 'https://private.example.com/source.vtt'
        self.event.save()
        response = self.client.get(self.url, **self.headers)
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()['code'], 'transcript_too_large')
