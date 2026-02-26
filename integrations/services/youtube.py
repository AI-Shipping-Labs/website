"""YouTube Data API v3 service for uploading videos.

Handles:
- OAuth2 token refresh (using stored refresh token from one-time consent)
- Resumable video upload to YouTube
- Setting video metadata (title, description, tags, privacy)
"""

import json
import logging
import time

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

YOUTUBE_TOKEN_URL = 'https://oauth2.googleapis.com/token'
YOUTUBE_UPLOAD_URL = 'https://www.googleapis.com/upload/youtube/v3/videos'

# Token cache (module-level, same pattern as zoom.py)
_token_cache = {
    'access_token': None,
    'expires_at': 0,
}


class YouTubeAPIError(Exception):
    """Raised when the YouTube API returns an error."""

    def __init__(self, message, status_code=None, response_data=None):
        self.status_code = status_code
        self.response_data = response_data
        super().__init__(message)


def get_access_token():
    """Get a valid YouTube OAuth2 access token using the refresh token.

    Uses cached token if still valid. Otherwise refreshes using the
    stored refresh token from the one-time OAuth consent flow.

    Returns:
        str: Valid access token.

    Raises:
        YouTubeAPIError: If token refresh fails.
    """
    now = time.time()

    # Return cached token if still valid (with 60s buffer)
    if _token_cache['access_token'] and _token_cache['expires_at'] > now + 60:
        return _token_cache['access_token']

    client_id = settings.YOUTUBE_CLIENT_ID
    client_secret = settings.YOUTUBE_CLIENT_SECRET
    refresh_token = settings.YOUTUBE_REFRESH_TOKEN

    if not all([client_id, client_secret, refresh_token]):
        raise YouTubeAPIError(
            'YouTube OAuth credentials not configured. '
            'Set YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET, and YOUTUBE_REFRESH_TOKEN.'
        )

    response = requests.post(
        YOUTUBE_TOKEN_URL,
        data={
            'client_id': client_id,
            'client_secret': client_secret,
            'refresh_token': refresh_token,
            'grant_type': 'refresh_token',
        },
        timeout=10,
    )

    if response.status_code != 200:
        raise YouTubeAPIError(
            f'Failed to refresh YouTube access token: {response.status_code}',
            status_code=response.status_code,
            response_data=response.json() if response.content else None,
        )

    data = response.json()
    _token_cache['access_token'] = data['access_token']
    _token_cache['expires_at'] = now + data.get('expires_in', 3600)

    return _token_cache['access_token']


def clear_token_cache():
    """Clear the cached access token (useful for testing)."""
    _token_cache['access_token'] = None
    _token_cache['expires_at'] = 0


def upload_video(file_path, title, description='', tags=None, privacy='unlisted'):
    """Upload a video file to YouTube via resumable upload.

    Args:
        file_path: Local path to the video file.
        title: Video title.
        description: Video description (optional).
        tags: List of tags (optional).
        privacy: Privacy status - 'public', 'unlisted', or 'private' (default 'unlisted').

    Returns:
        dict: {'video_id': str, 'youtube_url': str}

    Raises:
        YouTubeAPIError: If the upload fails.
    """
    token = get_access_token()

    # Build video metadata
    body = {
        'snippet': {
            'title': title[:100],  # YouTube title limit is 100 chars
            'description': description[:5000],  # YouTube description limit
            'tags': (tags or [])[:500],  # YouTube allows up to 500 tags
            'categoryId': '27',  # Education category
        },
        'status': {
            'privacyStatus': privacy,
            'selfDeclaredMadeForKids': False,
        },
    }

    # Step 1: Initiate resumable upload
    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json; charset=UTF-8',
        'X-Upload-Content-Type': 'video/mp4',
    }

    init_response = requests.post(
        YOUTUBE_UPLOAD_URL,
        params={
            'uploadType': 'resumable',
            'part': 'snippet,status',
        },
        headers=headers,
        data=json.dumps(body),
        timeout=30,
    )

    if init_response.status_code not in (200, 308):
        raise YouTubeAPIError(
            f'Failed to initiate YouTube upload: {init_response.status_code}',
            status_code=init_response.status_code,
            response_data=init_response.json() if init_response.content else None,
        )

    upload_url = init_response.headers.get('Location')
    if not upload_url:
        raise YouTubeAPIError(
            'YouTube upload initiation did not return a resumable upload URL',
        )

    # Step 2: Upload the video file
    with open(file_path, 'rb') as video_file:
        upload_response = requests.put(
            upload_url,
            data=video_file,
            headers={
                'Authorization': f'Bearer {token}',
                'Content-Type': 'video/mp4',
            },
            timeout=600,  # 10 min timeout for large uploads
        )

    if upload_response.status_code not in (200, 201):
        raise YouTubeAPIError(
            f'Failed to upload video to YouTube: {upload_response.status_code}',
            status_code=upload_response.status_code,
            response_data=upload_response.json() if upload_response.content else None,
        )

    data = upload_response.json()
    video_id = data['id']
    youtube_url = f'https://www.youtube.com/watch?v={video_id}'

    logger.info(
        'Uploaded video to YouTube: %s (title: "%s")',
        youtube_url, title,
    )

    return {
        'video_id': video_id,
        'youtube_url': youtube_url,
    }
