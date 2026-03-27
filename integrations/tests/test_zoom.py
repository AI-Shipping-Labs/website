"""Tests for Zoom Integration - issue #84.

Covers:
- Zoom service: token management, meeting creation, signature validation
- Zoom webhook endpoint: signature validation, recording.completed handling
- Event admin: auto-create Zoom meeting for new live events
- Recording creation from webhook with event linking
"""

import hashlib
import hmac
import json
import time
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from events.models import Event
from integrations.models import WebhookLog

User = get_user_model()

ZOOM_TEST_SECRET = 'test-zoom-webhook-secret'
ZOOM_TEST_CLIENT_ID = 'test-client-id'
ZOOM_TEST_CLIENT_SECRET = 'test-client-secret'
ZOOM_TEST_ACCOUNT_ID = 'test-account-id'


def make_zoom_signature(body, timestamp, secret=ZOOM_TEST_SECRET):
    """Create a valid Zoom webhook signature for testing."""
    message = f'v0:{timestamp}:{body}'
    sig = hmac.new(
        secret.encode('utf-8'),
        message.encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()
    return f'v0={sig}'


def make_recording_completed_payload(
    meeting_id,
    share_url='https://zoom.us/rec/share/abc',
    include_transcript=False,
):
    """Create a recording.completed webhook payload."""
    recording_files = [
        {
            'recording_type': 'shared_screen_with_speaker_view',
            'play_url': 'https://zoom.us/rec/play/abc123',
            'download_url': 'https://zoom.us/rec/download/abc123',
        },
        {
            'recording_type': 'audio_only',
            'play_url': 'https://zoom.us/rec/play/audio',
            'download_url': 'https://zoom.us/rec/download/audio',
        },
    ]
    if include_transcript:
        recording_files.append({
            'recording_type': 'audio_transcript',
            'file_type': 'VTT',
            'download_url': 'https://zoom.us/rec/download/transcript123.vtt',
            'file_size': 12345,
            'status': 'completed',
        })
    return {
        'event': 'recording.completed',
        'payload': {
            'object': {
                'id': meeting_id,
                'topic': 'Test Meeting',
                'share_url': share_url,
                'recording_files': recording_files,
            },
        },
    }


# --- Zoom Service Tests ---


class ZoomGetAccessTokenTest(TestCase):
    """Test Zoom OAuth token management."""

    def setUp(self):
        from integrations.services import zoom
        zoom.clear_token_cache()

    @override_settings(
        ZOOM_CLIENT_ID='', ZOOM_CLIENT_SECRET='', ZOOM_ACCOUNT_ID='',
    )
    def test_missing_credentials_raises_error(self):
        from integrations.services.zoom import ZoomAPIError, get_access_token
        with self.assertRaises(ZoomAPIError) as ctx:
            get_access_token()
        self.assertIn('not configured', str(ctx.exception))

    @override_settings(
        ZOOM_CLIENT_ID=ZOOM_TEST_CLIENT_ID,
        ZOOM_CLIENT_SECRET=ZOOM_TEST_CLIENT_SECRET,
        ZOOM_ACCOUNT_ID=ZOOM_TEST_ACCOUNT_ID,
    )
    @patch('integrations.services.zoom.requests.post')
    def test_successful_token_request(self, mock_post):
        from integrations.services.zoom import get_access_token
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'access_token': 'test-token-123',
            'expires_in': 3600,
        }
        mock_post.return_value = mock_response

        token = get_access_token()
        self.assertEqual(token, 'test-token-123')

        # Verify the request was made correctly
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        self.assertIn('account_credentials', str(call_kwargs))

    @override_settings(
        ZOOM_CLIENT_ID=ZOOM_TEST_CLIENT_ID,
        ZOOM_CLIENT_SECRET=ZOOM_TEST_CLIENT_SECRET,
        ZOOM_ACCOUNT_ID=ZOOM_TEST_ACCOUNT_ID,
    )
    @patch('integrations.services.zoom.requests.post')
    def test_cached_token_reused(self, mock_post):
        from integrations.services.zoom import get_access_token
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'access_token': 'cached-token',
            'expires_in': 3600,
        }
        mock_post.return_value = mock_response

        # First call fetches token
        token1 = get_access_token()
        # Second call should use cached token
        token2 = get_access_token()

        self.assertEqual(token1, 'cached-token')
        self.assertEqual(token2, 'cached-token')
        # Should only have made one HTTP request
        self.assertEqual(mock_post.call_count, 1)

    @override_settings(
        ZOOM_CLIENT_ID=ZOOM_TEST_CLIENT_ID,
        ZOOM_CLIENT_SECRET=ZOOM_TEST_CLIENT_SECRET,
        ZOOM_ACCOUNT_ID=ZOOM_TEST_ACCOUNT_ID,
    )
    @patch('integrations.services.zoom.requests.post')
    def test_failed_token_request_raises_error(self, mock_post):
        from integrations.services.zoom import ZoomAPIError, get_access_token
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.content = b'{"reason":"invalid"}'
        mock_response.json.return_value = {'reason': 'invalid'}
        mock_post.return_value = mock_response

        with self.assertRaises(ZoomAPIError) as ctx:
            get_access_token()
        self.assertEqual(ctx.exception.status_code, 401)


class ZoomCreateMeetingTest(TestCase):
    """Test Zoom meeting creation."""

    def setUp(self):
        from integrations.services import zoom
        zoom.clear_token_cache()
        self.event = Event.objects.create(
            title='Test Workshop',
            slug='test-workshop',
            event_type='live',
            start_datetime=timezone.now() + timedelta(days=7),
            end_datetime=timezone.now() + timedelta(days=7, hours=2),
            timezone='Europe/Berlin',
            status='draft',
        )

    @override_settings(
        ZOOM_CLIENT_ID=ZOOM_TEST_CLIENT_ID,
        ZOOM_CLIENT_SECRET=ZOOM_TEST_CLIENT_SECRET,
        ZOOM_ACCOUNT_ID=ZOOM_TEST_ACCOUNT_ID,
    )
    @patch('integrations.services.zoom.requests.post')
    def test_create_meeting_success(self, mock_post):
        from integrations.services.zoom import create_meeting

        # Mock token request
        token_response = MagicMock()
        token_response.status_code = 200
        token_response.json.return_value = {
            'access_token': 'token-abc',
            'expires_in': 3600,
        }

        # Mock meeting creation
        meeting_response = MagicMock()
        meeting_response.status_code = 201
        meeting_response.json.return_value = {
            'id': 98765432100,
            'join_url': 'https://zoom.us/j/98765432100',
            'topic': 'Test Workshop',
        }

        mock_post.side_effect = [token_response, meeting_response]

        result = create_meeting(self.event)

        self.assertEqual(result['meeting_id'], '98765432100')
        self.assertEqual(result['join_url'], 'https://zoom.us/j/98765432100')

        # Verify the meeting creation request
        meeting_call = mock_post.call_args_list[1]
        payload = meeting_call.kwargs.get('json') or meeting_call[1].get('json')
        self.assertEqual(payload['topic'], 'Test Workshop')
        self.assertEqual(payload['duration'], 120)  # 2 hours
        self.assertEqual(payload['timezone'], 'Europe/Berlin')
        self.assertEqual(payload['settings']['auto_recording'], 'cloud')
        self.assertTrue(payload['settings']['auto_transcribing'])

    @override_settings(
        ZOOM_CLIENT_ID=ZOOM_TEST_CLIENT_ID,
        ZOOM_CLIENT_SECRET=ZOOM_TEST_CLIENT_SECRET,
        ZOOM_ACCOUNT_ID=ZOOM_TEST_ACCOUNT_ID,
    )
    @patch('integrations.services.zoom.requests.post')
    def test_create_meeting_default_duration(self, mock_post):
        """Event without end_datetime defaults to 60 minutes."""
        from integrations.services.zoom import create_meeting

        self.event.end_datetime = None
        self.event.save()

        token_response = MagicMock()
        token_response.status_code = 200
        token_response.json.return_value = {
            'access_token': 'token-abc',
            'expires_in': 3600,
        }

        meeting_response = MagicMock()
        meeting_response.status_code = 201
        meeting_response.json.return_value = {
            'id': 11111111111,
            'join_url': 'https://zoom.us/j/11111111111',
        }

        mock_post.side_effect = [token_response, meeting_response]

        result = create_meeting(self.event)
        self.assertEqual(result['meeting_id'], '11111111111')

        meeting_call = mock_post.call_args_list[1]
        payload = meeting_call.kwargs.get('json') or meeting_call[1].get('json')
        self.assertEqual(payload['duration'], 60)

    @override_settings(
        ZOOM_CLIENT_ID=ZOOM_TEST_CLIENT_ID,
        ZOOM_CLIENT_SECRET=ZOOM_TEST_CLIENT_SECRET,
        ZOOM_ACCOUNT_ID=ZOOM_TEST_ACCOUNT_ID,
    )
    @patch('integrations.services.zoom.requests.post')
    def test_create_meeting_api_error(self, mock_post):
        from integrations.services.zoom import ZoomAPIError, create_meeting

        token_response = MagicMock()
        token_response.status_code = 200
        token_response.json.return_value = {
            'access_token': 'token-abc',
            'expires_in': 3600,
        }

        error_response = MagicMock()
        error_response.status_code = 400
        error_response.content = b'{"code":300,"message":"invalid"}'
        error_response.json.return_value = {'code': 300, 'message': 'invalid'}

        mock_post.side_effect = [token_response, error_response]

        with self.assertRaises(ZoomAPIError) as ctx:
            create_meeting(self.event)
        self.assertEqual(ctx.exception.status_code, 400)


class ZoomValidateWebhookSignatureTest(TestCase):
    """Test Zoom webhook signature validation."""

    @override_settings(ZOOM_WEBHOOK_SECRET_TOKEN=ZOOM_TEST_SECRET)
    def test_valid_signature(self):
        from integrations.services.zoom import validate_webhook_signature

        body = '{"event":"test"}'
        timestamp = str(int(time.time()))
        signature = make_zoom_signature(body, timestamp)

        request = MagicMock()
        request.headers = {
            'x-zm-request-timestamp': timestamp,
            'x-zm-signature': signature,
        }
        request.body = body.encode('utf-8')

        self.assertTrue(validate_webhook_signature(request))

    @override_settings(ZOOM_WEBHOOK_SECRET_TOKEN=ZOOM_TEST_SECRET)
    def test_invalid_signature(self):
        from integrations.services.zoom import validate_webhook_signature

        body = '{"event":"test"}'
        timestamp = str(int(time.time()))

        request = MagicMock()
        request.headers = {
            'x-zm-request-timestamp': timestamp,
            'x-zm-signature': 'v0=invalidsignature',
        }
        request.body = body.encode('utf-8')

        self.assertFalse(validate_webhook_signature(request))

    @override_settings(ZOOM_WEBHOOK_SECRET_TOKEN=ZOOM_TEST_SECRET)
    def test_missing_headers(self):
        from integrations.services.zoom import validate_webhook_signature

        request = MagicMock()
        request.headers = {}
        request.body = b'{}'

        self.assertFalse(validate_webhook_signature(request))

    @override_settings(ZOOM_WEBHOOK_SECRET_TOKEN='')
    def test_missing_secret_token(self):
        from integrations.services.zoom import validate_webhook_signature

        request = MagicMock()
        request.headers = {
            'x-zm-request-timestamp': '123',
            'x-zm-signature': 'v0=abc',
        }
        request.body = b'{}'

        self.assertFalse(validate_webhook_signature(request))

    @override_settings(ZOOM_WEBHOOK_SECRET_TOKEN=ZOOM_TEST_SECRET)
    def test_tampered_body_fails(self):
        from integrations.services.zoom import validate_webhook_signature

        body = '{"event":"test"}'
        timestamp = str(int(time.time()))
        signature = make_zoom_signature(body, timestamp)

        # Tamper with the body
        request = MagicMock()
        request.headers = {
            'x-zm-request-timestamp': timestamp,
            'x-zm-signature': signature,
        }
        request.body = b'{"event":"tampered"}'

        self.assertFalse(validate_webhook_signature(request))


# --- Zoom Webhook Endpoint Tests ---


@override_settings(ZOOM_WEBHOOK_SECRET_TOKEN=ZOOM_TEST_SECRET)
class ZoomWebhookEndpointTest(TestCase):
    """Test POST /api/webhooks/zoom endpoint."""

    def setUp(self):
        self.client = Client()

    def _post_webhook(self, payload_dict):
        """Helper to post a webhook with valid signature."""
        body = json.dumps(payload_dict)
        timestamp = str(int(time.time()))
        signature = make_zoom_signature(body, timestamp)
        return self.client.post(
            '/api/webhooks/zoom',
            data=body,
            content_type='application/json',
            HTTP_X_ZM_REQUEST_TIMESTAMP=timestamp,
            HTTP_X_ZM_SIGNATURE=signature,
        )

    def test_valid_webhook_returns_200(self):
        payload = {'event': 'meeting.started', 'payload': {}}
        response = self._post_webhook(payload)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], 'ok')

    def test_invalid_signature_returns_400(self):
        response = self.client.post(
            '/api/webhooks/zoom',
            data='{"event":"test"}',
            content_type='application/json',
            HTTP_X_ZM_REQUEST_TIMESTAMP='123',
            HTTP_X_ZM_SIGNATURE='v0=invalidsig',
        )
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertEqual(data['error'], 'Invalid webhook signature')

    def test_missing_signature_returns_400(self):
        response = self.client.post(
            '/api/webhooks/zoom',
            data='{"event":"test"}',
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)

    def test_webhook_logged(self):
        payload = {'event': 'meeting.ended', 'payload': {}}
        self._post_webhook(payload)
        log = WebhookLog.objects.filter(service='zoom').first()
        self.assertIsNotNone(log)
        self.assertEqual(log.event_type, 'meeting.ended')
        self.assertEqual(log.payload, payload)

    def test_get_not_allowed(self):
        response = self.client.get('/api/webhooks/zoom')
        self.assertEqual(response.status_code, 405)

    def test_csrf_exempt(self):
        """Webhook endpoint must work without CSRF token."""
        # The _post_webhook helper does not send a CSRF token,
        # so if this returns 200, CSRF is properly disabled.
        payload = {'event': 'test.event', 'payload': {}}
        response = self._post_webhook(payload)
        self.assertEqual(response.status_code, 200)


@override_settings(ZOOM_WEBHOOK_SECRET_TOKEN=ZOOM_TEST_SECRET)
class ZoomWebhookUrlValidationTest(TestCase):
    """Test Zoom endpoint URL validation (challenge/response)."""

    def setUp(self):
        self.client = Client()

    def test_url_validation_challenge(self):
        payload = {
            'event': 'endpoint.url_validation',
            'payload': {
                'plainToken': 'test-plain-token-abc',
            },
        }
        body = json.dumps(payload)
        timestamp = str(int(time.time()))
        signature = make_zoom_signature(body, timestamp)

        response = self.client.post(
            '/api/webhooks/zoom',
            data=body,
            content_type='application/json',
            HTTP_X_ZM_REQUEST_TIMESTAMP=timestamp,
            HTTP_X_ZM_SIGNATURE=signature,
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['plainToken'], 'test-plain-token-abc')
        self.assertIn('encryptedToken', data)

        # Verify the encrypted token is correct
        expected_encrypted = hmac.new(
            ZOOM_TEST_SECRET.encode('utf-8'),
            b'test-plain-token-abc',
            hashlib.sha256,
        ).hexdigest()
        self.assertEqual(data['encryptedToken'], expected_encrypted)


# --- Recording.completed Webhook Tests ---


@override_settings(ZOOM_WEBHOOK_SECRET_TOKEN=ZOOM_TEST_SECRET)
class ZoomRecordingCompletedTest(TestCase):
    """Test recording.completed webhook creates Recording and updates Event."""

    def setUp(self):
        self.client = Client()
        self.event = Event.objects.create(
            title='Workshop: Building AI Agents',
            slug='workshop-building-ai-agents',
            description='Learn how to build AI agents.',
            event_type='live',
            start_datetime=timezone.now() - timedelta(hours=3),
            end_datetime=timezone.now() - timedelta(hours=1),
            timezone='Europe/Berlin',
            zoom_meeting_id='12345678901',
            zoom_join_url='https://zoom.us/j/12345678901',
            tags=['ai', 'agents'],
            required_level=10,
            status='live',
        )

    def _post_webhook(self, payload_dict):
        body = json.dumps(payload_dict)
        timestamp = str(int(time.time()))
        signature = make_zoom_signature(body, timestamp)
        return self.client.post(
            '/api/webhooks/zoom',
            data=body,
            content_type='application/json',
            HTTP_X_ZM_REQUEST_TIMESTAMP=timestamp,
            HTTP_X_ZM_SIGNATURE=signature,
        )

    def test_sets_recording_fields_on_event(self):
        """recording.completed webhook sets recording fields on the matched Event."""
        payload = make_recording_completed_payload('12345678901')
        response = self._post_webhook(payload)
        self.assertEqual(response.status_code, 200)

        # Verify Event was updated with recording fields
        self.event.refresh_from_db()
        self.assertTrue(self.event.has_recording)
        self.assertEqual(self.event.recording_url, 'https://zoom.us/rec/play/abc123')
        self.assertEqual(self.event.status, 'completed')
        self.assertFalse(self.event.published)  # Needs admin review

    def test_recording_fields_set_on_event(self):
        """Recording fields are set directly on the matched Event."""
        payload = make_recording_completed_payload('12345678901')
        self._post_webhook(payload)

        self.event.refresh_from_db()
        self.assertTrue(self.event.has_recording)
        self.assertEqual(self.event.recording_url, 'https://zoom.us/rec/play/abc123')
        self.assertEqual(self.event.status, 'completed')

    def test_event_status_set_to_completed(self):
        payload = make_recording_completed_payload('12345678901')
        self._post_webhook(payload)

        self.event.refresh_from_db()
        self.assertEqual(self.event.status, 'completed')

    def test_recording_uses_play_url(self):
        """Preferred recording type (shared_screen_with_speaker_view) play_url used."""
        payload = make_recording_completed_payload('12345678901')
        self._post_webhook(payload)

        recording = Event.objects.get(slug='workshop-building-ai-agents')
        self.assertEqual(recording.recording_url, 'https://zoom.us/rec/play/abc123')

    def test_recording_falls_back_to_share_url(self):
        """Falls back to share_url when no preferred recording type found."""
        payload = {
            'event': 'recording.completed',
            'payload': {
                'object': {
                    'id': '12345678901',
                    'share_url': 'https://zoom.us/rec/share/fallback',
                    'recording_files': [
                        {
                            'recording_type': 'chat_file',
                            'play_url': 'https://zoom.us/rec/play/chat',
                        },
                    ],
                },
            },
        }
        self._post_webhook(payload)

        recording = Event.objects.get(slug='workshop-building-ai-agents')
        self.assertEqual(recording.recording_url, 'https://zoom.us/rec/share/fallback')

    def test_no_matching_event_ignored(self):
        """Webhook for unknown meeting ID is logged but event not updated."""
        payload = make_recording_completed_payload('99999999999')
        response = self._post_webhook(payload)
        self.assertEqual(response.status_code, 200)
        # The existing event should NOT have been updated with recording fields
        self.event.refresh_from_db()
        self.assertFalse(self.event.has_recording)

    def test_webhook_log_marked_processed(self):
        payload = make_recording_completed_payload('12345678901')
        self._post_webhook(payload)

        log = WebhookLog.objects.filter(
            service='zoom',
            event_type='recording.completed',
        ).first()
        self.assertIsNotNone(log)
        self.assertTrue(log.processed)

    def test_recording_fields_updated_on_existing_event(self):
        """Webhook updates existing event rather than creating new row."""
        event_count_before = Event.objects.count()
        payload = make_recording_completed_payload('12345678901')
        self._post_webhook(payload)

        # No new event should be created
        self.assertEqual(Event.objects.count(), event_count_before)
        # Existing event should be updated
        self.event.refresh_from_db()
        self.assertTrue(self.event.has_recording)

    def test_recording_date_from_event_start(self):
        payload = make_recording_completed_payload('12345678901')
        self._post_webhook(payload)

        recording = Event.objects.get(slug='workshop-building-ai-agents')
        self.assertEqual(recording.start_datetime.date(), self.event.start_datetime.date())

    def test_transcript_url_stored_when_present(self):
        """When webhook includes an audio_transcript file, transcript_url is stored."""
        payload = make_recording_completed_payload(
            '12345678901', include_transcript=True,
        )
        response = self._post_webhook(payload)
        self.assertEqual(response.status_code, 200)

        recording = Event.objects.get(slug='workshop-building-ai-agents')
        self.assertEqual(
            recording.transcript_url,
            'https://zoom.us/rec/download/transcript123.vtt',
        )

    def test_transcript_url_empty_when_not_present(self):
        """When webhook has no audio_transcript file, transcript_url is empty."""
        payload = make_recording_completed_payload(
            '12345678901', include_transcript=False,
        )
        response = self._post_webhook(payload)
        self.assertEqual(response.status_code, 200)

        recording = Event.objects.get(slug='workshop-building-ai-agents')
        self.assertEqual(recording.transcript_url, '')

    def test_recording_created_normally_without_transcript(self):
        """Recording is still created with all fields when no transcript is present."""
        payload = make_recording_completed_payload(
            '12345678901', include_transcript=False,
        )
        self._post_webhook(payload)

        recording = Event.objects.get(slug='workshop-building-ai-agents')
        self.assertEqual(recording.title, 'Workshop: Building AI Agents')
        self.assertEqual(recording.transcript_url, '')
        self.assertEqual(recording.transcript_text, '')

        self.event.refresh_from_db()
        self.assertEqual(self.event.status, 'completed')


# --- Event Admin Zoom Meeting Tests ---


class EventAdminZoomCreationTest(TestCase):
    """Test that admin defers Zoom meeting creation to Studio."""

    def setUp(self):
        self.client = Client()
        self.admin_user = User.objects.create_superuser(
            email='admin@test.com', password='testpass',
        )
        self.client.login(email='admin@test.com', password='testpass')

    @override_settings(
        ZOOM_CLIENT_ID=ZOOM_TEST_CLIENT_ID,
        ZOOM_CLIENT_SECRET=ZOOM_TEST_CLIENT_SECRET,
        ZOOM_ACCOUNT_ID=ZOOM_TEST_ACCOUNT_ID,
    )
    @patch('integrations.services.zoom.requests.post')
    def test_new_live_event_does_not_auto_create_zoom_meeting(self, mock_post):
        """Creating a new live event via admin does not auto-create Zoom.

        Zoom meeting creation is handled via the Studio endpoint
        (POST /studio/events/<id>/create-zoom), not the admin save.
        """
        start = timezone.now() + timedelta(days=7)
        response = self.client.post('/admin/events/event/add/', {
            'title': 'Admin Live Event',
            'slug': 'admin-live-event',
            'description': 'Test event from admin',
            'event_type': 'live',
            'platform': 'zoom',
            'start_datetime_0': start.strftime('%Y-%m-%d'),
            'start_datetime_1': start.strftime('%H:%M:%S'),
            'timezone': 'Europe/Berlin',
            'zoom_meeting_id': '',
            'zoom_join_url': '',
            'location': '',
            'tags': '[]',
            'required_level': 0,
            'status': 'draft',
            'registrations-TOTAL_FORMS': '0',
            'registrations-INITIAL_FORMS': '0',
            'registrations-MIN_NUM_FORMS': '0',
            'registrations-MAX_NUM_FORMS': '1000',
        })

        # Event should be saved but Zoom fields remain empty
        event = Event.objects.get(slug='admin-live-event')
        self.assertEqual(event.zoom_meeting_id, '')
        self.assertEqual(event.zoom_join_url, '')
        # No Zoom API calls should have been made
        mock_post.assert_not_called()

    @override_settings(
        ZOOM_CLIENT_ID=ZOOM_TEST_CLIENT_ID,
        ZOOM_CLIENT_SECRET=ZOOM_TEST_CLIENT_SECRET,
        ZOOM_ACCOUNT_ID=ZOOM_TEST_ACCOUNT_ID,
    )
    @patch('integrations.services.zoom.requests.post')
    def test_async_event_does_not_create_zoom_meeting(self, mock_post):
        """Creating an async event should not trigger Zoom meeting creation."""
        start = timezone.now() + timedelta(days=7)
        response = self.client.post('/admin/events/event/add/', {
            'title': 'Admin Async Event',
            'slug': 'admin-async-event',
            'description': 'Async event',
            'event_type': 'async',
            'platform': 'zoom',
            'start_datetime_0': start.strftime('%Y-%m-%d'),
            'start_datetime_1': start.strftime('%H:%M:%S'),
            'timezone': 'Europe/Berlin',
            'zoom_meeting_id': '',
            'zoom_join_url': '',
            'location': '',
            'tags': '[]',
            'required_level': 0,
            'status': 'draft',
            'registrations-TOTAL_FORMS': '0',
            'registrations-INITIAL_FORMS': '0',
            'registrations-MIN_NUM_FORMS': '0',
            'registrations-MAX_NUM_FORMS': '1000',
        })

        event = Event.objects.get(slug='admin-async-event')
        self.assertEqual(event.zoom_meeting_id, '')
        self.assertEqual(event.zoom_join_url, '')
        # No Zoom API calls should have been made
        mock_post.assert_not_called()

    @override_settings(
        ZOOM_CLIENT_ID=ZOOM_TEST_CLIENT_ID,
        ZOOM_CLIENT_SECRET=ZOOM_TEST_CLIENT_SECRET,
        ZOOM_ACCOUNT_ID=ZOOM_TEST_ACCOUNT_ID,
    )
    @patch('integrations.services.zoom.requests.post')
    def test_live_event_with_existing_zoom_id_not_overwritten(self, mock_post):
        """If zoom_meeting_id already provided, don't create a new meeting."""
        start = timezone.now() + timedelta(days=7)
        response = self.client.post('/admin/events/event/add/', {
            'title': 'Pre-Zoomed Event',
            'slug': 'pre-zoomed-event',
            'description': 'Already has Zoom',
            'event_type': 'live',
            'platform': 'zoom',
            'start_datetime_0': start.strftime('%Y-%m-%d'),
            'start_datetime_1': start.strftime('%H:%M:%S'),
            'timezone': 'Europe/Berlin',
            'zoom_meeting_id': '99999999',
            'zoom_join_url': 'https://zoom.us/j/99999999',
            'location': 'Zoom',
            'tags': '[]',
            'required_level': 0,
            'status': 'draft',
            'registrations-TOTAL_FORMS': '0',
            'registrations-INITIAL_FORMS': '0',
            'registrations-MIN_NUM_FORMS': '0',
            'registrations-MAX_NUM_FORMS': '1000',
        })

        event = Event.objects.get(slug='pre-zoomed-event')
        self.assertEqual(event.zoom_meeting_id, '99999999')
        self.assertEqual(event.zoom_join_url, 'https://zoom.us/j/99999999')
        mock_post.assert_not_called()

    @override_settings(
        ZOOM_CLIENT_ID='', ZOOM_CLIENT_SECRET='', ZOOM_ACCOUNT_ID='',
    )
    def test_zoom_failure_still_saves_event(self):
        """If Zoom API fails, the event should still be saved."""
        start = timezone.now() + timedelta(days=7)
        response = self.client.post('/admin/events/event/add/', {
            'title': 'Zoom Fail Event',
            'slug': 'zoom-fail-event',
            'description': 'Zoom will fail',
            'event_type': 'live',
            'platform': 'zoom',
            'start_datetime_0': start.strftime('%Y-%m-%d'),
            'start_datetime_1': start.strftime('%H:%M:%S'),
            'timezone': 'Europe/Berlin',
            'zoom_meeting_id': '',
            'zoom_join_url': '',
            'location': '',
            'tags': '[]',
            'required_level': 0,
            'status': 'draft',
            'registrations-TOTAL_FORMS': '0',
            'registrations-INITIAL_FORMS': '0',
            'registrations-MIN_NUM_FORMS': '0',
            'registrations-MAX_NUM_FORMS': '1000',
        })

        # Event should still be created even though Zoom failed
        event = Event.objects.filter(slug='zoom-fail-event').first()
        self.assertIsNotNone(event)
        self.assertEqual(event.title, 'Zoom Fail Event')
        # Zoom fields should remain empty
        self.assertEqual(event.zoom_meeting_id, '')
        self.assertEqual(event.zoom_join_url, '')

    @override_settings(
        ZOOM_CLIENT_ID=ZOOM_TEST_CLIENT_ID,
        ZOOM_CLIENT_SECRET=ZOOM_TEST_CLIENT_SECRET,
        ZOOM_ACCOUNT_ID=ZOOM_TEST_ACCOUNT_ID,
    )
    @patch('integrations.services.zoom.requests.post')
    def test_editing_existing_event_does_not_create_zoom_meeting(self, mock_post):
        """Editing an existing live event should not trigger meeting creation."""
        from integrations.services import zoom
        zoom.clear_token_cache()

        event = Event.objects.create(
            title='Existing Event',
            slug='existing-event',
            event_type='live',
            start_datetime=timezone.now() + timedelta(days=7),
            timezone='Europe/Berlin',
            zoom_meeting_id='existing-meeting-id',
            zoom_join_url='https://zoom.us/j/existing',
            status='upcoming',
        )

        start = event.start_datetime
        response = self.client.post(
            f'/admin/events/event/{event.pk}/change/', {
                'title': 'Existing Event Updated',
                'slug': 'existing-event',
                'description': 'Updated description',
                'event_type': 'live',
                'platform': 'zoom',
                'start_datetime_0': start.strftime('%Y-%m-%d'),
                'start_datetime_1': start.strftime('%H:%M:%S'),
                'timezone': 'Europe/Berlin',
                'zoom_meeting_id': 'existing-meeting-id',
                'zoom_join_url': 'https://zoom.us/j/existing',
                'location': '',
                'tags': '[]',
                'required_level': 0,
                'status': 'upcoming',
                'registrations-TOTAL_FORMS': '0',
                'registrations-INITIAL_FORMS': '0',
                'registrations-MIN_NUM_FORMS': '0',
                'registrations-MAX_NUM_FORMS': '1000',
            },
        )

        event.refresh_from_db()
        self.assertEqual(event.title, 'Existing Event Updated')
        # Zoom API should not have been called
        mock_post.assert_not_called()


# --- Settings Configuration Test ---


class ZoomSettingsTest(TestCase):
    """Test that Zoom configuration settings are properly loaded."""

    @override_settings(
        ZOOM_CLIENT_ID='cid123',
        ZOOM_CLIENT_SECRET='csec456',
        ZOOM_ACCOUNT_ID='aid789',
        ZOOM_WEBHOOK_SECRET_TOKEN='wsec000',
    )
    def test_zoom_settings_available(self):
        from django.conf import settings
        self.assertEqual(settings.ZOOM_CLIENT_ID, 'cid123')
        self.assertEqual(settings.ZOOM_CLIENT_SECRET, 'csec456')
        self.assertEqual(settings.ZOOM_ACCOUNT_ID, 'aid789')
        self.assertEqual(settings.ZOOM_WEBHOOK_SECRET_TOKEN, 'wsec000')

    def test_zoom_settings_default_empty(self):
        """Settings default to empty string when env vars not set."""
        from django.conf import settings
        # These should exist and be strings (possibly empty from env)
        self.assertIsInstance(settings.ZOOM_CLIENT_ID, str)
        self.assertIsInstance(settings.ZOOM_CLIENT_SECRET, str)
        self.assertIsInstance(settings.ZOOM_ACCOUNT_ID, str)
        self.assertIsInstance(settings.ZOOM_WEBHOOK_SECRET_TOKEN, str)


# --- Recording Model Event FK Test ---


class EventRecordingFieldsTest(TestCase):
    """Test that Event model has recording fields (merged from Recording)."""

    def test_event_has_recording_url(self):
        event = Event.objects.create(
            title='Recording Fields Test',
            slug='rec-fields-test',
            start_datetime=timezone.now(), status='completed',
            recording_url='https://youtube.com/watch?v=test',
        )
        self.assertTrue(event.has_recording)

    def test_event_without_recording(self):
        event = Event.objects.create(
            title='No Recording Test',
            slug='no-rec-test',
            start_datetime=timezone.now(), status='completed',
        )
        self.assertFalse(event.has_recording)

    def test_video_url_property(self):
        event = Event.objects.create(
            title='Video URL Test',
            slug='video-url-test',
            start_datetime=timezone.now(), status='completed',
            recording_s3_url='https://s3.example.com/vid.mp4',
            recording_url='https://youtube.com/watch?v=test',
        )
        self.assertEqual(event.video_url, 'https://s3.example.com/vid.mp4')
