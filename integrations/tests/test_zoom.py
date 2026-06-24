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
import os
import tempfile
import time
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from events.models import Event
from integrations.models import ContentSource, WebhookLog

User = get_user_model()

ZOOM_TEST_SECRET = 'test-zoom-webhook-secret'
ZOOM_TEST_CLIENT_ID = 'test-client-id'
ZOOM_TEST_CLIENT_SECRET = 'test-client-secret'
ZOOM_TEST_ACCOUNT_ID = 'test-account-id'


class _ZoomSecretIsolationMixin:
    """Isolate Zoom webhook-secret tests from leaked config state.

    ``validate_webhook_signature`` resolves ``ZOOM_WEBHOOK_SECRET_TOKEN`` via
    ``get_config``, which returns a DB ``IntegrationSetting`` override (and an
    in-process cache of it) BEFORE the Django ``settings`` value. Under CI
    sharding another test can persist a row or warm the cache for that key,
    which then wins over this suite's ``@override_settings`` and makes the
    signed request fail validation (issue #939). Clearing any override row and
    the config cache before each test forces ``get_config`` to fall through to
    the overridden ``settings`` value deterministically.
    """

    def setUp(self):
        super().setUp()
        from integrations.config import clear_config_cache
        from integrations.models import IntegrationSetting

        IntegrationSetting.objects.filter(
            key='ZOOM_WEBHOOK_SECRET_TOKEN'
        ).delete()
        clear_config_cache()
        self.addCleanup(clear_config_cache)


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
    def test_start_time_is_event_local_wall_clock(self, mock_post):
        """start_time is the event-LOCAL wall clock, not the UTC wall clock (#996).

        Zoom reads a ``start_time`` without offset as local time in the given
        ``timezone``. The event is at 15:00 UTC == 17:00 Europe/Berlin (summer),
        so the payload must send 17:00 + timezone=Europe/Berlin, otherwise the
        meeting lands 2 hours early.
        """
        from integrations.services.zoom import create_meeting

        # 2026-07-01 15:00 UTC -> 17:00 Europe/Berlin (CEST, UTC+2).
        self.event.start_datetime = datetime(
            2026, 7, 1, 15, 0, tzinfo=dt_timezone.utc,
        )
        self.event.end_datetime = datetime(
            2026, 7, 1, 16, 0, tzinfo=dt_timezone.utc,
        )
        self.event.timezone = 'Europe/Berlin'
        self.event.save()

        token_response = MagicMock()
        token_response.status_code = 200
        token_response.json.return_value = {
            'access_token': 'token-abc', 'expires_in': 3600,
        }
        meeting_response = MagicMock()
        meeting_response.status_code = 201
        meeting_response.json.return_value = {
            'id': 55555555555, 'join_url': 'https://zoom.us/j/55555555555',
        }
        mock_post.side_effect = [token_response, meeting_response]

        create_meeting(self.event)

        meeting_call = mock_post.call_args_list[1]
        payload = meeting_call.kwargs.get('json') or meeting_call[1].get('json')
        self.assertEqual(payload['start_time'], '2026-07-01T17:00:00')
        self.assertEqual(payload['timezone'], 'Europe/Berlin')

    @override_settings(
        ZOOM_CLIENT_ID=ZOOM_TEST_CLIENT_ID,
        ZOOM_CLIENT_SECRET=ZOOM_TEST_CLIENT_SECRET,
        ZOOM_ACCOUNT_ID=ZOOM_TEST_ACCOUNT_ID,
    )
    @patch('integrations.services.zoom.requests.post')
    def test_blank_timezone_falls_back_to_utc(self, mock_post):
        """A blank/invalid timezone falls back to UTC instead of raising (#996)."""
        from integrations.services.zoom import create_meeting

        self.event.start_datetime = datetime(
            2026, 7, 1, 15, 0, tzinfo=dt_timezone.utc,
        )
        self.event.end_datetime = datetime(
            2026, 7, 1, 16, 0, tzinfo=dt_timezone.utc,
        )
        self.event.timezone = ''
        self.event.save()

        token_response = MagicMock()
        token_response.status_code = 200
        token_response.json.return_value = {
            'access_token': 'token-abc', 'expires_in': 3600,
        }
        meeting_response = MagicMock()
        meeting_response.status_code = 201
        meeting_response.json.return_value = {
            'id': 66666666666, 'join_url': 'https://zoom.us/j/66666666666',
        }
        mock_post.side_effect = [token_response, meeting_response]

        create_meeting(self.event)

        meeting_call = mock_post.call_args_list[1]
        payload = meeting_call.kwargs.get('json') or meeting_call[1].get('json')
        # UTC fallback: start_time is the UTC wall clock and timezone is UTC.
        self.assertEqual(payload['start_time'], '2026-07-01T15:00:00')
        self.assertEqual(payload['timezone'], 'UTC')

    @override_settings(
        ZOOM_CLIENT_ID=ZOOM_TEST_CLIENT_ID,
        ZOOM_CLIENT_SECRET=ZOOM_TEST_CLIENT_SECRET,
        ZOOM_ACCOUNT_ID=ZOOM_TEST_ACCOUNT_ID,
    )
    @patch('integrations.services.zoom.requests.post')
    def test_invalid_timezone_falls_back_to_utc(self, mock_post):
        """An unrecognised timezone string falls back to UTC, not an error (#996)."""
        from integrations.services.zoom import create_meeting

        self.event.start_datetime = datetime(
            2026, 7, 1, 15, 0, tzinfo=dt_timezone.utc,
        )
        self.event.end_datetime = datetime(
            2026, 7, 1, 16, 0, tzinfo=dt_timezone.utc,
        )
        self.event.timezone = 'Not/AZone'
        self.event.save()

        token_response = MagicMock()
        token_response.status_code = 200
        token_response.json.return_value = {
            'access_token': 'token-abc', 'expires_in': 3600,
        }
        meeting_response = MagicMock()
        meeting_response.status_code = 201
        meeting_response.json.return_value = {
            'id': 77777777777, 'join_url': 'https://zoom.us/j/77777777777',
        }
        mock_post.side_effect = [token_response, meeting_response]

        create_meeting(self.event)

        meeting_call = mock_post.call_args_list[1]
        payload = meeting_call.kwargs.get('json') or meeting_call[1].get('json')
        self.assertEqual(payload['start_time'], '2026-07-01T15:00:00')
        self.assertEqual(payload['timezone'], 'UTC')

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


@override_settings(
    ZOOM_CLIENT_ID=ZOOM_TEST_CLIENT_ID,
    ZOOM_CLIENT_SECRET=ZOOM_TEST_CLIENT_SECRET,
    ZOOM_ACCOUNT_ID=ZOOM_TEST_ACCOUNT_ID,
)
class ZoomWaitingRoomSettingsTest(TestCase):
    """Waiting-room / join-before-host config wiring for create + patch (#1004)."""

    def setUp(self):
        from integrations.config import clear_config_cache
        from integrations.models import IntegrationSetting
        from integrations.services import zoom

        zoom.clear_token_cache()
        # Isolate from any leaked override rows / warmed cache for these keys.
        IntegrationSetting.objects.filter(
            key__in=['ZOOM_WAITING_ROOM', 'ZOOM_JOIN_BEFORE_HOST'],
        ).delete()
        clear_config_cache()
        self.addCleanup(clear_config_cache)

        self.event = Event.objects.create(
            title='Waiting Room Workshop',
            slug='waiting-room-workshop',
            start_datetime=timezone.now() + timedelta(days=7),
            end_datetime=timezone.now() + timedelta(days=7, hours=1),
            timezone='Europe/Berlin',
            zoom_meeting_id='12121212121',
            zoom_join_url='https://zoom.us/j/12121212121',
            status='upcoming',
        )

    def _set_override(self, key, value):
        from integrations.config import clear_config_cache
        from integrations.models import IntegrationSetting

        IntegrationSetting.objects.update_or_create(
            key=key, defaults={'value': value},
        )
        clear_config_cache()

    def _create_payload(self, mock_post):
        from integrations.services.zoom import create_meeting

        token_response = MagicMock()
        token_response.status_code = 200
        token_response.json.return_value = {
            'access_token': 'token-abc', 'expires_in': 3600,
        }
        meeting_response = MagicMock()
        meeting_response.status_code = 201
        meeting_response.json.return_value = {
            'id': 98765432100, 'join_url': 'https://zoom.us/j/98765432100',
        }
        mock_post.side_effect = [token_response, meeting_response]

        create_meeting(self.event)
        meeting_call = mock_post.call_args_list[1]
        return meeting_call.kwargs.get('json') or meeting_call[1].get('json')

    @patch('integrations.services.zoom.requests.post')
    def test_create_meeting_defaults_join_before_host_off(self, mock_post):
        # join_before_host OFF + waiting_room OFF by default: early joiners hit
        # Zoom's "waiting for the host" hold and recording waits for the host,
        # with no manual admitting (#1004 revised).
        payload = self._create_payload(mock_post)
        self.assertIs(payload['settings']['join_before_host'], False)
        self.assertIs(payload['settings']['waiting_room'], False)

    @patch('integrations.services.zoom.requests.post')
    def test_create_meeting_respects_waiting_room_override(self, mock_post):
        # The waiting-room key is still wired: flipping it to true produces
        # waiting_room=true even though it defaults off.
        self._set_override('ZOOM_WAITING_ROOM', 'true')
        payload = self._create_payload(mock_post)
        self.assertIs(payload['settings']['waiting_room'], True)

    @patch('integrations.services.zoom.requests.post')
    def test_create_meeting_respects_join_before_host_override(self, mock_post):
        self._set_override('ZOOM_JOIN_BEFORE_HOST', 'true')
        payload = self._create_payload(mock_post)
        self.assertIs(payload['settings']['join_before_host'], True)

    @patch('integrations.services.zoom.requests.post')
    def test_create_meeting_keeps_auto_recording_cloud(self, mock_post):
        payload = self._create_payload(mock_post)
        self.assertEqual(payload['settings']['auto_recording'], 'cloud')
        self.assertIs(payload['settings']['mute_upon_entry'], True)
        self.assertIs(payload['settings']['auto_transcribing'], True)

    @patch('integrations.services.zoom.requests.patch')
    @patch('integrations.services.zoom.requests.post')
    def test_update_meeting_patches_schedule_payload(self, mock_post, mock_patch):
        from integrations.services.zoom import update_meeting

        self.event.title = 'Retitled Workshop'
        self.event.start_datetime = datetime(
            2026, 7, 1, 15, 0, tzinfo=dt_timezone.utc,
        )
        self.event.end_datetime = datetime(
            2026, 7, 1, 17, 0, tzinfo=dt_timezone.utc,
        )
        self.event.timezone = 'Europe/Berlin'
        self.event.save()

        token_response = MagicMock()
        token_response.status_code = 200
        token_response.json.return_value = {
            'access_token': 'token-abc', 'expires_in': 3600,
        }
        mock_post.return_value = token_response

        patch_response = MagicMock()
        patch_response.status_code = 204
        patch_response.content = b''
        mock_patch.return_value = patch_response

        update_meeting(self.event)

        called_url = mock_patch.call_args.args[0]
        self.assertTrue(called_url.endswith('/meetings/12121212121'))
        body = mock_patch.call_args.kwargs['json']
        self.assertEqual(body['topic'], 'Retitled Workshop')
        self.assertEqual(body['start_time'], '2026-07-01T17:00:00')
        self.assertEqual(body['duration'], 120)
        self.assertEqual(body['timezone'], 'Europe/Berlin')
        self.assertIn('settings', body)
        self.assertNotIn('type', body)

    @patch('integrations.services.zoom.requests.delete')
    @patch('integrations.services.zoom.requests.post')
    def test_delete_meeting_404_is_success(self, mock_post, mock_delete):
        from integrations.services.zoom import delete_meeting

        token_response = MagicMock()
        token_response.status_code = 200
        token_response.json.return_value = {
            'access_token': 'token-abc', 'expires_in': 3600,
        }
        mock_post.return_value = token_response

        delete_response = MagicMock()
        delete_response.status_code = 404
        delete_response.content = b''
        mock_delete.return_value = delete_response

        delete_meeting(self.event)
        called_url = mock_delete.call_args.args[0]
        self.assertTrue(called_url.endswith('/meetings/12121212121'))

    @patch('integrations.services.zoom.requests.delete')
    @patch('integrations.services.zoom.requests.post')
    def test_delete_meeting_raises_on_error(self, mock_post, mock_delete):
        from integrations.services.zoom import ZoomAPIError, delete_meeting

        token_response = MagicMock()
        token_response.status_code = 200
        token_response.json.return_value = {
            'access_token': 'token-abc', 'expires_in': 3600,
        }
        mock_post.return_value = token_response

        delete_response = MagicMock()
        delete_response.status_code = 403
        delete_response.content = b'{"code":124,"message":"not allowed"}'
        delete_response.json.return_value = {
            'code': 124, 'message': 'not allowed',
        }
        mock_delete.return_value = delete_response

        with self.assertRaises(ZoomAPIError) as ctx:
            delete_meeting(self.event)
        self.assertEqual(ctx.exception.status_code, 403)

    @patch('integrations.services.zoom.requests.patch')
    @patch('integrations.services.zoom.requests.post')
    def test_update_meeting_settings_patches_settings_only(
        self, mock_post, mock_patch,
    ):
        from integrations.services.zoom import update_meeting_settings

        token_response = MagicMock()
        token_response.status_code = 200
        token_response.json.return_value = {
            'access_token': 'token-abc', 'expires_in': 3600,
        }
        mock_post.return_value = token_response

        patch_response = MagicMock()
        patch_response.status_code = 204
        patch_response.content = b''
        mock_patch.return_value = patch_response

        update_meeting_settings(self.event)

        mock_patch.assert_called_once()
        called_url = mock_patch.call_args.args[0]
        self.assertTrue(called_url.endswith('/meetings/12121212121'))

        body = mock_patch.call_args.kwargs['json']
        # Body is settings-only — recreating (topic/start_time/duration) would
        # change the join URL, which we must not do.
        self.assertEqual(list(body.keys()), ['settings'])
        self.assertNotIn('topic', body)
        self.assertNotIn('start_time', body)
        self.assertNotIn('duration', body)
        self.assertIs(body['settings']['waiting_room'], False)
        self.assertIs(body['settings']['join_before_host'], False)

    @patch('integrations.services.zoom.requests.patch')
    @patch('integrations.services.zoom.requests.post')
    def test_update_meeting_settings_204_is_success(self, mock_post, mock_patch):
        from integrations.services.zoom import update_meeting_settings

        token_response = MagicMock()
        token_response.status_code = 200
        token_response.json.return_value = {
            'access_token': 'token-abc', 'expires_in': 3600,
        }
        mock_post.return_value = token_response

        patch_response = MagicMock()
        patch_response.status_code = 204
        patch_response.content = b''
        mock_patch.return_value = patch_response

        # No exception means 204 is treated as success.
        update_meeting_settings(self.event)

    @patch('integrations.services.zoom.requests.patch')
    @patch('integrations.services.zoom.requests.post')
    def test_update_meeting_settings_raises_on_error(self, mock_post, mock_patch):
        from integrations.services.zoom import ZoomAPIError, update_meeting_settings

        token_response = MagicMock()
        token_response.status_code = 200
        token_response.json.return_value = {
            'access_token': 'token-abc', 'expires_in': 3600,
        }
        mock_post.return_value = token_response

        error_response = MagicMock()
        error_response.status_code = 400
        error_response.content = b'{"code":300,"message":"invalid"}'
        error_response.json.return_value = {'code': 300, 'message': 'invalid'}
        mock_patch.return_value = error_response

        with self.assertRaises(ZoomAPIError) as ctx:
            update_meeting_settings(self.event)
        self.assertEqual(ctx.exception.status_code, 400)


class ApplyZoomMeetingSettingsCommandTest(TestCase):
    """apply_zoom_meeting_settings command selection + resilience (#1004)."""

    def setUp(self):
        # Upcoming event WITH a meeting id — the one target.
        self.upcoming_with_id = Event.objects.create(
            title='Upcoming With Id',
            slug='upcoming-with-id',
            start_datetime=timezone.now() + timedelta(days=3),
            end_datetime=timezone.now() + timedelta(days=3, hours=1),
            timezone='UTC',
            zoom_meeting_id='10000000001',
            zoom_join_url='https://zoom.us/j/10000000001',
            status='upcoming',
        )
        # Past event with a meeting id — must be skipped.
        self.past_with_id = Event.objects.create(
            title='Past With Id',
            slug='past-with-id',
            start_datetime=timezone.now() - timedelta(days=3, hours=1),
            end_datetime=timezone.now() - timedelta(days=3),
            timezone='UTC',
            zoom_meeting_id='10000000002',
            zoom_join_url='https://zoom.us/j/10000000002',
            status='upcoming',
        )
        # Upcoming event WITHOUT a meeting id — must be skipped.
        self.upcoming_no_id = Event.objects.create(
            title='Upcoming No Id',
            slug='upcoming-no-id',
            start_datetime=timezone.now() + timedelta(days=4),
            end_datetime=timezone.now() + timedelta(days=4, hours=1),
            timezone='UTC',
            zoom_meeting_id='',
            status='upcoming',
        )

    @patch('events.management.commands.apply_zoom_meeting_settings.update_meeting_settings')
    def test_command_patches_upcoming_with_id_only(self, mock_update):
        from django.core.management import call_command

        call_command('apply_zoom_meeting_settings')

        self.assertEqual(mock_update.call_count, 1)
        patched_event = mock_update.call_args.args[0]
        self.assertEqual(patched_event.pk, self.upcoming_with_id.pk)

    @patch('events.management.commands.apply_zoom_meeting_settings.update_meeting_settings')
    def test_dry_run_makes_no_calls(self, mock_update):
        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        call_command('apply_zoom_meeting_settings', '--dry-run', stdout=out)

        mock_update.assert_not_called()
        self.assertIn('Upcoming With Id', out.getvalue())

    @patch('events.management.commands.apply_zoom_meeting_settings.update_meeting_settings')
    def test_continues_after_per_event_failure(self, mock_update):
        from io import StringIO

        from django.core.management import call_command

        from integrations.services.zoom import ZoomAPIError

        # Second upcoming event with an id so we have two targets.
        Event.objects.create(
            title='Upcoming With Id Two',
            slug='upcoming-with-id-two',
            start_datetime=timezone.now() + timedelta(days=5),
            end_datetime=timezone.now() + timedelta(days=5, hours=1),
            timezone='UTC',
            zoom_meeting_id='10000000003',
            zoom_join_url='https://zoom.us/j/10000000003',
            status='upcoming',
        )

        mock_update.side_effect = [
            ZoomAPIError('boom', status_code=400),
            None,
        ]

        out = StringIO()
        err = StringIO()
        call_command(
            'apply_zoom_meeting_settings', stdout=out, stderr=err,
        )

        # Both upcoming-with-id events were attempted despite the first failing.
        self.assertEqual(mock_update.call_count, 2)
        self.assertIn('Patched 1 meeting(s), failed 1.', out.getvalue())


class ZoomValidateWebhookSignatureTest(_ZoomSecretIsolationMixin, TestCase):
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
class ZoomWebhookEndpointTest(_ZoomSecretIsolationMixin, TestCase):
    """Test POST /api/webhooks/zoom endpoint."""

    def setUp(self):
        super().setUp()
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
class ZoomWebhookUrlValidationTest(_ZoomSecretIsolationMixin, TestCase):
    """Test Zoom endpoint URL validation (challenge/response)."""

    def setUp(self):
        super().setUp()
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
class ZoomRecordingCompletedTest(_ZoomSecretIsolationMixin, TestCase):
    """Test recording.completed webhook creates Recording and updates Event."""

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.event = Event.objects.create(
            title='Workshop: Building AI Agents',
            slug='workshop-building-ai-agents',
            description='Learn how to build AI agents.',
            start_datetime=timezone.now() - timedelta(hours=3),
            end_datetime=timezone.now() - timedelta(hours=1),
            timezone='Europe/Berlin',
            zoom_meeting_id='12345678901',
            zoom_join_url='https://zoom.us/j/12345678901',
            tags=['ai', 'agents'],
            required_level=10,
            status='upcoming',
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
        """recording.completed webhook sets recording fields on the matched Event.

        Issue #713: the webhook no longer writes ``status='completed'``;
        ``Event.is_past`` is time-derived. Stored status stays
        ``upcoming`` until the daily cron refreshes it.
        """
        payload = make_recording_completed_payload('12345678901')
        response = self._post_webhook(payload)
        self.assertEqual(response.status_code, 200)

        # Verify Event was updated with recording fields
        self.event.refresh_from_db()
        self.assertTrue(self.event.has_recording)
        self.assertEqual(self.event.recording_url, 'https://zoom.us/rec/play/abc123')
        self.assertFalse(self.event.published)  # Needs admin review

    def test_recording_fields_set_on_event(self):
        """Recording fields are set directly on the matched Event."""
        payload = make_recording_completed_payload('12345678901')
        self._post_webhook(payload)

        self.event.refresh_from_db()
        self.assertTrue(self.event.has_recording)
        self.assertEqual(self.event.recording_url, 'https://zoom.us/rec/play/abc123')

    def test_event_status_not_written_by_webhook(self):
        """Issue #713: the webhook MUST NOT flip ``status`` to ``completed``.

        Status flipping is now the responsibility of the daily
        ``complete_finished_events`` cron; the user-facing UI no longer
        depends on the stored field.
        """
        payload = make_recording_completed_payload('12345678901')
        self._post_webhook(payload)

        self.event.refresh_from_db()
        self.assertEqual(self.event.status, 'upcoming')

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
        self.client.post('/admin/events/event/add/', {
            'title': 'Admin Live Event',
            'slug': 'admin-live-event',
            'description': 'Test event from admin',
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
            # EventInstructor through-model inline (issue #308)
            'eventinstructor_set-TOTAL_FORMS': '0',
            'eventinstructor_set-INITIAL_FORMS': '0',
            'eventinstructor_set-MIN_NUM_FORMS': '0',
            'eventinstructor_set-MAX_NUM_FORMS': '1000',
            # EventFeedback inline (issue #679)
            'feedback-TOTAL_FORMS': '0',
            'feedback-INITIAL_FORMS': '0',
            'feedback-MIN_NUM_FORMS': '0',
            'feedback-MAX_NUM_FORMS': '1000',
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
        self.client.post('/admin/events/event/add/', {
            'title': 'Admin Async Event',
            'slug': 'admin-async-event',
            'description': 'Async event',
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
            # EventInstructor through-model inline (issue #308)
            'eventinstructor_set-TOTAL_FORMS': '0',
            'eventinstructor_set-INITIAL_FORMS': '0',
            'eventinstructor_set-MIN_NUM_FORMS': '0',
            'eventinstructor_set-MAX_NUM_FORMS': '1000',
            # EventFeedback inline (issue #679)
            'feedback-TOTAL_FORMS': '0',
            'feedback-INITIAL_FORMS': '0',
            'feedback-MIN_NUM_FORMS': '0',
            'feedback-MAX_NUM_FORMS': '1000',
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
        self.client.post('/admin/events/event/add/', {
            'title': 'Pre-Zoomed Event',
            'slug': 'pre-zoomed-event',
            'description': 'Already has Zoom',
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
            # EventInstructor through-model inline (issue #308)
            'eventinstructor_set-TOTAL_FORMS': '0',
            'eventinstructor_set-INITIAL_FORMS': '0',
            'eventinstructor_set-MIN_NUM_FORMS': '0',
            'eventinstructor_set-MAX_NUM_FORMS': '1000',
            # EventFeedback inline (issue #679)
            'feedback-TOTAL_FORMS': '0',
            'feedback-INITIAL_FORMS': '0',
            'feedback-MIN_NUM_FORMS': '0',
            'feedback-MAX_NUM_FORMS': '1000',
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
        self.client.post('/admin/events/event/add/', {
            'title': 'Zoom Fail Event',
            'slug': 'zoom-fail-event',
            'description': 'Zoom will fail',
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
            # EventInstructor through-model inline (issue #308)
            'eventinstructor_set-TOTAL_FORMS': '0',
            'eventinstructor_set-INITIAL_FORMS': '0',
            'eventinstructor_set-MIN_NUM_FORMS': '0',
            'eventinstructor_set-MAX_NUM_FORMS': '1000',
            # EventFeedback inline (issue #679)
            'feedback-TOTAL_FORMS': '0',
            'feedback-INITIAL_FORMS': '0',
            'feedback-MIN_NUM_FORMS': '0',
            'feedback-MAX_NUM_FORMS': '1000',
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
            start_datetime=timezone.now() + timedelta(days=7),
            timezone='Europe/Berlin',
            zoom_meeting_id='existing-meeting-id',
            zoom_join_url='https://zoom.us/j/existing',
            status='upcoming',
        )

        start = event.start_datetime
        self.client.post(
            f'/admin/events/event/{event.pk}/change/', {
                'title': 'Existing Event Updated',
                'slug': 'existing-event',
                'description': 'Updated description',
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
                # EventInstructor through-model inline (issue #308)
                'eventinstructor_set-TOTAL_FORMS': '0',
                'eventinstructor_set-INITIAL_FORMS': '0',
                'eventinstructor_set-MIN_NUM_FORMS': '0',
                'eventinstructor_set-MAX_NUM_FORMS': '1000',
                # EventFeedback inline (issue #679)
                'feedback-TOTAL_FORMS': '0',
                'feedback-INITIAL_FORMS': '0',
                'feedback-MIN_NUM_FORMS': '0',
                'feedback-MAX_NUM_FORMS': '1000',
            },
        )

        event.refresh_from_db()
        self.assertEqual(event.title, 'Existing Event Updated')
        # Zoom API should not have been called
        mock_post.assert_not_called()


class EventSyncZoomCreationTest(TestCase):
    """Test auto-creating Zoom meetings during event sync."""

    def setUp(self):
        from integrations.services import zoom

        zoom.clear_token_cache()

    def _make_source(self):
        return ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
        )

    def _write_event_yaml(
        self,
        tmp_dir,
        *,
        slug='synced-zoom-event',
        title='Synced Zoom Event',
        event_type='',
        status='',
        start_datetime='',
        end_datetime='',
        timezone_name='',
        location='',
        platform='',
    ):
        events_dir = os.path.join(tmp_dir, 'events')
        os.makedirs(events_dir, exist_ok=True)
        with open(os.path.join(events_dir, f'{slug}.yaml'), 'w') as f:
            f.write('content_id: "11111111-1111-1111-1111-111111111111"\n')
            f.write(f'title: "{title}"\n')
            f.write(f'slug: "{slug}"\n')
            f.write('description: "Synced from content repo"\n')
            f.write('published_at: "2026-04-22"\n')
            if event_type:
                f.write(f'event_type: "{event_type}"\n')
            if status:
                f.write(f'status: "{status}"\n')
            if start_datetime:
                f.write(f'start_datetime: "{start_datetime}"\n')
            if end_datetime:
                f.write(f'end_datetime: "{end_datetime}"\n')
            if timezone_name:
                f.write(f'timezone: "{timezone_name}"\n')
            if location:
                f.write(f'location: "{location}"\n')
            if platform:
                f.write(f'platform: "{platform}"\n')
        return events_dir

    def _sync_events(self, source, events_dir, commit_sha='abc1234'):
        # Issue #310: per-type orchestrators are gone — go through
        # ``sync_content_source`` so the walker dispatches to events.
        from integrations.services.github import sync_content_source

        # ``events_dir`` here is actually the repo root (tmp_dir/events).
        # The walker expects to find events under an ``events/`` subtree;
        # since the test wrote directly to ``events_dir``, walk one level
        # up to find the repo root.
        repo_dir = os.path.dirname(events_dir)
        return sync_content_source(source, repo_dir=repo_dir)

    @patch('integrations.services.zoom.create_meeting')
    def test_sync_creates_zoom_meeting_for_location_zoom_event(
        self, mock_create_meeting,
    ):
        mock_create_meeting.return_value = {
            'meeting_id': '12345678900',
            'join_url': 'https://zoom.us/j/12345678900',
        }
        source = self._make_source()

        with tempfile.TemporaryDirectory() as tmp_dir:
            events_dir = self._write_event_yaml(
                tmp_dir,
                status='upcoming',
                start_datetime='2026-05-01T18:00:00Z',
                end_datetime='2026-05-01T19:30:00Z',
                timezone_name='Europe/Berlin',
                location='Zoom',
            )
            sync_log = self._sync_events(source, events_dir)

        event = Event.objects.get(slug='synced-zoom-event')
        self.assertEqual(sync_log.errors, [])
        self.assertEqual(event.status, 'upcoming')
        self.assertEqual(
            event.start_datetime,
            datetime(2026, 5, 1, 18, 0, tzinfo=dt_timezone.utc),
        )
        self.assertEqual(
            event.end_datetime,
            datetime(2026, 5, 1, 19, 30, tzinfo=dt_timezone.utc),
        )
        self.assertEqual(event.timezone, 'Europe/Berlin')
        self.assertEqual(event.location, 'Zoom')
        self.assertEqual(event.zoom_meeting_id, '12345678900')
        self.assertEqual(event.zoom_join_url, 'https://zoom.us/j/12345678900')
        self.assertEqual(mock_create_meeting.call_count, 1)
        self.assertEqual(mock_create_meeting.call_args.args[0].slug, event.slug)

    @patch('integrations.services.zoom.create_meeting')
    def test_sync_creates_zoom_meeting_for_platform_zoom_event(
        self, mock_create_meeting,
    ):
        mock_create_meeting.return_value = {
            'meeting_id': '22222222222',
            'join_url': 'https://zoom.us/j/22222222222',
        }
        source = self._make_source()

        with tempfile.TemporaryDirectory() as tmp_dir:
            events_dir = self._write_event_yaml(
                tmp_dir,
                slug='platform-zoom-event',
                title='Platform Zoom Event',
                status='draft',
                start_datetime='2026-05-03T09:00:00Z',
                timezone_name='UTC',
                location='Community Room',
                platform='zoom',
            )
            self._sync_events(source, events_dir)

        event = Event.objects.get(slug='platform-zoom-event')
        self.assertEqual(event.platform, 'zoom')
        self.assertEqual(event.zoom_meeting_id, '22222222222')
        self.assertEqual(event.zoom_join_url, 'https://zoom.us/j/22222222222')
        self.assertEqual(mock_create_meeting.call_count, 1)

    @patch('integrations.services.zoom.create_meeting')
    def test_sync_without_zoom_frontmatter_does_not_create_meeting(
        self, mock_create_meeting,
    ):
        source = self._make_source()

        with tempfile.TemporaryDirectory() as tmp_dir:
            events_dir = self._write_event_yaml(
                tmp_dir,
                slug='recording-only-event',
                title='Recording Only Event',
                status='upcoming',
                start_datetime='2026-05-04T12:00:00Z',
                platform='custom',
                location='Campus',
            )
            self._sync_events(source, events_dir)

        event = Event.objects.get(slug='recording-only-event')
        self.assertEqual(event.zoom_meeting_id, '')
        self.assertEqual(event.zoom_join_url, '')
        mock_create_meeting.assert_not_called()

    @patch('integrations.services.zoom.create_meeting')
    def test_completed_sync_event_does_not_create_zoom_meeting(
        self, mock_create_meeting,
    ):
        source = self._make_source()

        with tempfile.TemporaryDirectory() as tmp_dir:
            events_dir = self._write_event_yaml(
                tmp_dir,
                slug='completed-zoom-event',
                title='Completed Zoom Event',
                status='completed',
                start_datetime='2026-05-05T12:00:00Z',
                location='Zoom',
            )
            self._sync_events(source, events_dir)

        event = Event.objects.get(slug='completed-zoom-event')
        self.assertEqual(event.status, 'completed')
        self.assertEqual(event.zoom_meeting_id, '')
        self.assertEqual(event.zoom_join_url, '')
        mock_create_meeting.assert_not_called()

    @patch('integrations.services.zoom.create_meeting')
    def test_cancelled_sync_event_does_not_create_zoom_meeting(
        self, mock_create_meeting,
    ):
        source = self._make_source()

        with tempfile.TemporaryDirectory() as tmp_dir:
            events_dir = self._write_event_yaml(
                tmp_dir,
                slug='cancelled-zoom-event',
                title='Cancelled Zoom Event',
                status='cancelled',
                start_datetime='2026-05-05T12:00:00Z',
                platform='zoom',
            )
            self._sync_events(source, events_dir)

        event = Event.objects.get(slug='cancelled-zoom-event')
        self.assertEqual(event.status, 'cancelled')
        self.assertEqual(event.zoom_meeting_id, '')
        self.assertEqual(event.zoom_join_url, '')
        mock_create_meeting.assert_not_called()

    @patch('integrations.services.zoom.create_meeting')
    def test_recording_style_row_without_start_datetime_skips_zoom_creation(
        self, mock_create_meeting,
    ):
        source = self._make_source()

        with tempfile.TemporaryDirectory() as tmp_dir:
            events_dir = self._write_event_yaml(
                tmp_dir,
                slug='no-schedule-event',
                title='No Schedule Event',
                status='upcoming',
                location='Zoom',
            )
            self._sync_events(source, events_dir)

        event = Event.objects.get(slug='no-schedule-event')
        self.assertEqual(event.status, 'upcoming')
        self.assertEqual(event.zoom_meeting_id, '')
        self.assertEqual(event.zoom_join_url, '')
        mock_create_meeting.assert_not_called()

    @patch('integrations.services.zoom.create_meeting')
    def test_sync_update_does_not_create_duplicate_zoom_meeting(
        self, mock_create_meeting,
    ):
        mock_create_meeting.return_value = {
            'meeting_id': '33333333333',
            'join_url': 'https://zoom.us/j/33333333333',
        }
        source = self._make_source()

        with tempfile.TemporaryDirectory() as tmp_dir:
            events_dir = self._write_event_yaml(
                tmp_dir,
                slug='existing-zoom-event',
                title='Original Title',
                status='upcoming',
                start_datetime='2026-05-06T12:00:00Z',
                location='Zoom',
            )
            self._sync_events(source, events_dir, commit_sha='abc1234')

            events_dir = self._write_event_yaml(
                tmp_dir,
                slug='existing-zoom-event',
                title='Updated Title',
                status='upcoming',
                start_datetime='2026-05-06T12:00:00Z',
                location='Zoom',
            )
            self._sync_events(source, events_dir, commit_sha='def5678')

        event = Event.objects.get(slug='existing-zoom-event')
        self.assertEqual(event.title, 'Updated Title')
        self.assertEqual(event.zoom_meeting_id, '33333333333')
        self.assertEqual(event.zoom_join_url, 'https://zoom.us/j/33333333333')
        self.assertEqual(mock_create_meeting.call_count, 1)

    @patch('integrations.services.zoom.create_meeting')
    def test_zoom_api_failure_does_not_break_sync(self, mock_create_meeting):
        from integrations.services.zoom import ZoomAPIError

        mock_create_meeting.side_effect = ZoomAPIError(
            'Failed to create Zoom meeting: 429',
            status_code=429,
        )
        source = self._make_source()

        with tempfile.TemporaryDirectory() as tmp_dir:
            events_dir = self._write_event_yaml(
                tmp_dir,
                slug='rate-limited-event',
                title='Rate Limited Event',
                status='upcoming',
                start_datetime='2026-05-07T12:00:00Z',
                location='Zoom',
            )
            with self.assertLogs('integrations.services.github', level='WARNING') as logs:
                sync_log = self._sync_events(source, events_dir)

        event = Event.objects.get(slug='rate-limited-event')
        self.assertEqual(sync_log.errors, [])
        self.assertEqual(sync_log.items_created, 1)
        self.assertEqual(event.zoom_meeting_id, '')
        self.assertEqual(event.zoom_join_url, '')
        self.assertIn(
            'Failed to auto-create Zoom meeting for synced event rate-limited-event',
            '\n'.join(logs.output),
        )

    @override_settings(
        ZOOM_CLIENT_ID='',
        ZOOM_CLIENT_SECRET='',
        ZOOM_ACCOUNT_ID='',
    )
    def test_sync_skips_zoom_creation_when_credentials_missing(self):
        from integrations.services import zoom

        zoom.clear_token_cache()
        source = self._make_source()

        with tempfile.TemporaryDirectory() as tmp_dir:
            events_dir = self._write_event_yaml(
                tmp_dir,
                slug='missing-creds-event',
                title='Missing Credentials Event',
                status='upcoming',
                start_datetime='2026-05-08T12:00:00Z',
                location='Zoom',
            )
            with self.assertLogs('integrations.services.github', level='WARNING') as logs:
                sync_log = self._sync_events(source, events_dir)

        event = Event.objects.get(slug='missing-creds-event')
        self.assertEqual(sync_log.errors, [])
        self.assertEqual(event.zoom_meeting_id, '')
        self.assertEqual(event.zoom_join_url, '')
        self.assertIn('Zoom OAuth credentials not configured', '\n'.join(logs.output))


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
