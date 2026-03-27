"""Tests for YouTube Data API v3 service - issue #111.

Covers:
- Token refresh: successful refresh, cached token reuse, missing credentials, API error
- Video upload: successful upload, initiation failure, upload failure, missing upload URL
- Settings: YouTube credentials loaded from settings
"""

import json
from unittest.mock import MagicMock, mock_open, patch

from django.test import TestCase, override_settings

YOUTUBE_TEST_CLIENT_ID = 'test-yt-client-id'
YOUTUBE_TEST_CLIENT_SECRET = 'test-yt-client-secret'
YOUTUBE_TEST_REFRESH_TOKEN = 'test-yt-refresh-token'


class YouTubeGetAccessTokenTest(TestCase):
    """Test YouTube OAuth2 token refresh."""

    def setUp(self):
        from integrations.services import youtube
        youtube.clear_token_cache()

    @override_settings(
        YOUTUBE_CLIENT_ID='', YOUTUBE_CLIENT_SECRET='', YOUTUBE_REFRESH_TOKEN='',
    )
    def test_missing_credentials_raises_error(self):
        from integrations.services.youtube import YouTubeAPIError, get_access_token
        with self.assertRaises(YouTubeAPIError) as ctx:
            get_access_token()
        self.assertIn('not configured', str(ctx.exception))

    @override_settings(
        YOUTUBE_CLIENT_ID=YOUTUBE_TEST_CLIENT_ID,
        YOUTUBE_CLIENT_SECRET=YOUTUBE_TEST_CLIENT_SECRET,
        YOUTUBE_REFRESH_TOKEN=YOUTUBE_TEST_REFRESH_TOKEN,
    )
    @patch('integrations.services.youtube.requests.post')
    def test_successful_token_refresh(self, mock_post):
        from integrations.services.youtube import get_access_token
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'access_token': 'yt-token-123',
            'expires_in': 3600,
        }
        mock_post.return_value = mock_response

        token = get_access_token()
        self.assertEqual(token, 'yt-token-123')

        # Verify the request was made correctly
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        self.assertIn('refresh_token', str(call_kwargs))

    @override_settings(
        YOUTUBE_CLIENT_ID=YOUTUBE_TEST_CLIENT_ID,
        YOUTUBE_CLIENT_SECRET=YOUTUBE_TEST_CLIENT_SECRET,
        YOUTUBE_REFRESH_TOKEN=YOUTUBE_TEST_REFRESH_TOKEN,
    )
    @patch('integrations.services.youtube.requests.post')
    def test_cached_token_reused(self, mock_post):
        from integrations.services.youtube import get_access_token
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'access_token': 'cached-yt-token',
            'expires_in': 3600,
        }
        mock_post.return_value = mock_response

        # First call fetches token
        token1 = get_access_token()
        # Second call should use cached token
        token2 = get_access_token()

        self.assertEqual(token1, 'cached-yt-token')
        self.assertEqual(token2, 'cached-yt-token')
        # Should only have made one HTTP request
        self.assertEqual(mock_post.call_count, 1)

    @override_settings(
        YOUTUBE_CLIENT_ID=YOUTUBE_TEST_CLIENT_ID,
        YOUTUBE_CLIENT_SECRET=YOUTUBE_TEST_CLIENT_SECRET,
        YOUTUBE_REFRESH_TOKEN=YOUTUBE_TEST_REFRESH_TOKEN,
    )
    @patch('integrations.services.youtube.requests.post')
    def test_failed_token_refresh_raises_error(self, mock_post):
        from integrations.services.youtube import YouTubeAPIError, get_access_token
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.content = b'{"error":"invalid_grant"}'
        mock_response.json.return_value = {'error': 'invalid_grant'}
        mock_post.return_value = mock_response

        with self.assertRaises(YouTubeAPIError) as ctx:
            get_access_token()
        self.assertEqual(ctx.exception.status_code, 401)

    @override_settings(
        YOUTUBE_CLIENT_ID=YOUTUBE_TEST_CLIENT_ID,
        YOUTUBE_CLIENT_SECRET=YOUTUBE_TEST_CLIENT_SECRET,
        YOUTUBE_REFRESH_TOKEN=YOUTUBE_TEST_REFRESH_TOKEN,
    )
    @patch('integrations.services.youtube.requests.post')
    def test_token_refresh_sends_correct_params(self, mock_post):
        from integrations.services.youtube import get_access_token
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'access_token': 'token-abc',
            'expires_in': 3600,
        }
        mock_post.return_value = mock_response

        get_access_token()

        call_kwargs = mock_post.call_args
        data = call_kwargs.kwargs.get('data') or call_kwargs[1].get('data')
        self.assertEqual(data['client_id'], YOUTUBE_TEST_CLIENT_ID)
        self.assertEqual(data['client_secret'], YOUTUBE_TEST_CLIENT_SECRET)
        self.assertEqual(data['refresh_token'], YOUTUBE_TEST_REFRESH_TOKEN)
        self.assertEqual(data['grant_type'], 'refresh_token')


class YouTubeClearTokenCacheTest(TestCase):
    """Test that clear_token_cache resets the module-level cache."""

    def test_clear_token_cache(self):
        from integrations.services.youtube import _token_cache, clear_token_cache
        _token_cache['access_token'] = 'something'
        _token_cache['expires_at'] = 9999999999
        clear_token_cache()
        self.assertIsNone(_token_cache['access_token'])
        self.assertEqual(_token_cache['expires_at'], 0)


class YouTubeUploadVideoTest(TestCase):
    """Test YouTube video upload."""

    def setUp(self):
        from integrations.services import youtube
        youtube.clear_token_cache()

    @override_settings(
        YOUTUBE_CLIENT_ID=YOUTUBE_TEST_CLIENT_ID,
        YOUTUBE_CLIENT_SECRET=YOUTUBE_TEST_CLIENT_SECRET,
        YOUTUBE_REFRESH_TOKEN=YOUTUBE_TEST_REFRESH_TOKEN,
    )
    @patch('builtins.open', mock_open(read_data=b'fake-video-data'))
    @patch('integrations.services.youtube.requests.put')
    @patch('integrations.services.youtube.requests.post')
    def test_successful_upload(self, mock_post, mock_put):
        from integrations.services.youtube import upload_video

        # Mock token refresh
        token_response = MagicMock()
        token_response.status_code = 200
        token_response.json.return_value = {
            'access_token': 'yt-token',
            'expires_in': 3600,
        }

        # Mock upload initiation
        init_response = MagicMock()
        init_response.status_code = 200
        init_response.headers = {'Location': 'https://www.googleapis.com/upload/resumable/abc'}

        mock_post.side_effect = [token_response, init_response]

        # Mock video upload
        upload_response = MagicMock()
        upload_response.status_code = 200
        upload_response.json.return_value = {'id': 'abc123XYZ'}
        mock_put.return_value = upload_response

        result = upload_video(
            file_path='/tmp/test.mp4',
            title='Test Workshop Recording',
            description='A test video',
            tags=['ai', 'workshop'],
            privacy='unlisted',
        )

        self.assertEqual(result['video_id'], 'abc123XYZ')
        self.assertEqual(result['youtube_url'], 'https://www.youtube.com/watch?v=abc123XYZ')

    @override_settings(
        YOUTUBE_CLIENT_ID=YOUTUBE_TEST_CLIENT_ID,
        YOUTUBE_CLIENT_SECRET=YOUTUBE_TEST_CLIENT_SECRET,
        YOUTUBE_REFRESH_TOKEN=YOUTUBE_TEST_REFRESH_TOKEN,
    )
    @patch('integrations.services.youtube.requests.post')
    def test_upload_initiation_failure(self, mock_post):
        from integrations.services.youtube import YouTubeAPIError, upload_video

        # Mock token refresh
        token_response = MagicMock()
        token_response.status_code = 200
        token_response.json.return_value = {
            'access_token': 'yt-token',
            'expires_in': 3600,
        }

        # Mock failed initiation
        init_response = MagicMock()
        init_response.status_code = 403
        init_response.content = b'{"error":"quotaExceeded"}'
        init_response.json.return_value = {'error': 'quotaExceeded'}

        mock_post.side_effect = [token_response, init_response]

        with self.assertRaises(YouTubeAPIError) as ctx:
            upload_video('/tmp/test.mp4', 'Test')
        self.assertEqual(ctx.exception.status_code, 403)

    @override_settings(
        YOUTUBE_CLIENT_ID=YOUTUBE_TEST_CLIENT_ID,
        YOUTUBE_CLIENT_SECRET=YOUTUBE_TEST_CLIENT_SECRET,
        YOUTUBE_REFRESH_TOKEN=YOUTUBE_TEST_REFRESH_TOKEN,
    )
    @patch('integrations.services.youtube.requests.post')
    def test_missing_upload_url(self, mock_post):
        from integrations.services.youtube import YouTubeAPIError, upload_video

        # Mock token refresh
        token_response = MagicMock()
        token_response.status_code = 200
        token_response.json.return_value = {
            'access_token': 'yt-token',
            'expires_in': 3600,
        }

        # Mock initiation without Location header
        init_response = MagicMock()
        init_response.status_code = 200
        init_response.headers = {}

        mock_post.side_effect = [token_response, init_response]

        with self.assertRaises(YouTubeAPIError) as ctx:
            upload_video('/tmp/test.mp4', 'Test')
        self.assertIn('resumable upload URL', str(ctx.exception))

    @override_settings(
        YOUTUBE_CLIENT_ID=YOUTUBE_TEST_CLIENT_ID,
        YOUTUBE_CLIENT_SECRET=YOUTUBE_TEST_CLIENT_SECRET,
        YOUTUBE_REFRESH_TOKEN=YOUTUBE_TEST_REFRESH_TOKEN,
    )
    @patch('builtins.open', mock_open(read_data=b'fake-video-data'))
    @patch('integrations.services.youtube.requests.put')
    @patch('integrations.services.youtube.requests.post')
    def test_upload_failure(self, mock_post, mock_put):
        from integrations.services.youtube import YouTubeAPIError, upload_video

        # Mock token refresh
        token_response = MagicMock()
        token_response.status_code = 200
        token_response.json.return_value = {
            'access_token': 'yt-token',
            'expires_in': 3600,
        }

        # Mock successful initiation
        init_response = MagicMock()
        init_response.status_code = 200
        init_response.headers = {'Location': 'https://www.googleapis.com/upload/resumable/abc'}

        mock_post.side_effect = [token_response, init_response]

        # Mock failed upload
        upload_response = MagicMock()
        upload_response.status_code = 500
        upload_response.content = b'{"error":"internalError"}'
        upload_response.json.return_value = {'error': 'internalError'}
        mock_put.return_value = upload_response

        with self.assertRaises(YouTubeAPIError) as ctx:
            upload_video('/tmp/test.mp4', 'Test')
        self.assertEqual(ctx.exception.status_code, 500)

    @override_settings(
        YOUTUBE_CLIENT_ID=YOUTUBE_TEST_CLIENT_ID,
        YOUTUBE_CLIENT_SECRET=YOUTUBE_TEST_CLIENT_SECRET,
        YOUTUBE_REFRESH_TOKEN=YOUTUBE_TEST_REFRESH_TOKEN,
    )
    @patch('builtins.open', mock_open(read_data=b'fake-video-data'))
    @patch('integrations.services.youtube.requests.put')
    @patch('integrations.services.youtube.requests.post')
    def test_upload_sets_metadata(self, mock_post, mock_put):
        from integrations.services.youtube import upload_video

        # Mock token refresh
        token_response = MagicMock()
        token_response.status_code = 200
        token_response.json.return_value = {
            'access_token': 'yt-token',
            'expires_in': 3600,
        }

        # Mock upload initiation
        init_response = MagicMock()
        init_response.status_code = 200
        init_response.headers = {'Location': 'https://www.googleapis.com/upload/resumable/abc'}

        mock_post.side_effect = [token_response, init_response]

        # Mock video upload
        upload_response = MagicMock()
        upload_response.status_code = 200
        upload_response.json.return_value = {'id': 'vid123'}
        mock_put.return_value = upload_response

        upload_video(
            file_path='/tmp/test.mp4',
            title='My Video Title',
            description='My video description',
            tags=['tag1', 'tag2'],
            privacy='public',
        )

        # Check the initiation request body
        init_call = mock_post.call_args_list[1]
        body = json.loads(init_call.kwargs.get('data') or init_call[1].get('data'))
        self.assertEqual(body['snippet']['title'], 'My Video Title')
        self.assertEqual(body['snippet']['description'], 'My video description')
        self.assertEqual(body['snippet']['tags'], ['tag1', 'tag2'])
        self.assertEqual(body['snippet']['categoryId'], '27')
        self.assertEqual(body['status']['privacyStatus'], 'public')

    @override_settings(
        YOUTUBE_CLIENT_ID=YOUTUBE_TEST_CLIENT_ID,
        YOUTUBE_CLIENT_SECRET=YOUTUBE_TEST_CLIENT_SECRET,
        YOUTUBE_REFRESH_TOKEN=YOUTUBE_TEST_REFRESH_TOKEN,
    )
    @patch('builtins.open', mock_open(read_data=b'fake-video-data'))
    @patch('integrations.services.youtube.requests.put')
    @patch('integrations.services.youtube.requests.post')
    def test_upload_defaults_to_unlisted(self, mock_post, mock_put):
        from integrations.services.youtube import upload_video

        # Mock token refresh
        token_response = MagicMock()
        token_response.status_code = 200
        token_response.json.return_value = {
            'access_token': 'yt-token',
            'expires_in': 3600,
        }

        # Mock upload initiation
        init_response = MagicMock()
        init_response.status_code = 200
        init_response.headers = {'Location': 'https://www.googleapis.com/upload/resumable/abc'}

        mock_post.side_effect = [token_response, init_response]

        # Mock video upload
        upload_response = MagicMock()
        upload_response.status_code = 200
        upload_response.json.return_value = {'id': 'vid456'}
        mock_put.return_value = upload_response

        upload_video(file_path='/tmp/test.mp4', title='Test')

        # Check privacy defaults to unlisted
        init_call = mock_post.call_args_list[1]
        body = json.loads(init_call.kwargs.get('data') or init_call[1].get('data'))
        self.assertEqual(body['status']['privacyStatus'], 'unlisted')


class YouTubeSettingsTest(TestCase):
    """Test that YouTube configuration settings are properly loaded."""

    @override_settings(
        YOUTUBE_CLIENT_ID='yt-cid',
        YOUTUBE_CLIENT_SECRET='yt-csec',
        YOUTUBE_REFRESH_TOKEN='yt-rt',
    )
    def test_youtube_settings_available(self):
        from django.conf import settings
        self.assertEqual(settings.YOUTUBE_CLIENT_ID, 'yt-cid')
        self.assertEqual(settings.YOUTUBE_CLIENT_SECRET, 'yt-csec')
        self.assertEqual(settings.YOUTUBE_REFRESH_TOKEN, 'yt-rt')

    def test_youtube_settings_default_empty(self):
        """Settings default to empty string when env vars not set."""
        from django.conf import settings
        self.assertIsInstance(settings.YOUTUBE_CLIENT_ID, str)
        self.assertIsInstance(settings.YOUTUBE_CLIENT_SECRET, str)
        self.assertIsInstance(settings.YOUTUBE_REFRESH_TOKEN, str)
