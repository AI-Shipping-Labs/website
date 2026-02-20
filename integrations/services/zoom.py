"""Zoom API service for server-to-server OAuth integration.

Handles:
- OAuth token management (server-to-server flow)
- Creating Zoom meetings for live events
- Validating Zoom webhook signatures
"""

import hashlib
import hmac
import json
import logging
import time
from urllib.parse import urljoin

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

# Token cache (module-level for simplicity; in production use Redis/cache)
_token_cache = {
    'access_token': None,
    'expires_at': 0,
}

ZOOM_OAUTH_TOKEN_URL = 'https://zoom.us/oauth/token'
ZOOM_API_BASE_URL = 'https://api.zoom.us/v2/'


class ZoomAPIError(Exception):
    """Raised when the Zoom API returns an error."""

    def __init__(self, message, status_code=None, response_data=None):
        self.status_code = status_code
        self.response_data = response_data
        super().__init__(message)


def get_access_token():
    """Get a valid Zoom server-to-server OAuth access token.

    Uses cached token if still valid. Otherwise requests a new one
    from the Zoom OAuth endpoint using client credentials grant.

    Returns:
        str: Valid access token.

    Raises:
        ZoomAPIError: If token request fails.
    """
    now = time.time()

    # Return cached token if still valid (with 60s buffer)
    if _token_cache['access_token'] and _token_cache['expires_at'] > now + 60:
        return _token_cache['access_token']

    client_id = settings.ZOOM_CLIENT_ID
    client_secret = settings.ZOOM_CLIENT_SECRET
    account_id = settings.ZOOM_ACCOUNT_ID

    if not all([client_id, client_secret, account_id]):
        raise ZoomAPIError(
            'Zoom OAuth credentials not configured. '
            'Set ZOOM_CLIENT_ID, ZOOM_CLIENT_SECRET, and ZOOM_ACCOUNT_ID.'
        )

    response = requests.post(
        ZOOM_OAUTH_TOKEN_URL,
        params={'grant_type': 'account_credentials', 'account_id': account_id},
        auth=(client_id, client_secret),
        timeout=10,
    )

    if response.status_code != 200:
        raise ZoomAPIError(
            f'Failed to obtain Zoom access token: {response.status_code}',
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


def create_meeting(event):
    """Create a Zoom meeting for the given event.

    Args:
        event: Event model instance with title, start_datetime,
               end_datetime, and timezone fields.

    Returns:
        dict: {'meeting_id': str, 'join_url': str}

    Raises:
        ZoomAPIError: If the Zoom API call fails.
    """
    token = get_access_token()

    # Calculate duration in minutes
    duration = 60  # default 60 minutes
    if event.end_datetime and event.start_datetime:
        delta = event.end_datetime - event.start_datetime
        duration = max(1, int(delta.total_seconds() / 60))

    payload = {
        'topic': event.title,
        'type': 2,  # Scheduled meeting
        'start_time': event.start_datetime.strftime('%Y-%m-%dT%H:%M:%S'),
        'duration': duration,
        'timezone': event.timezone,
        'settings': {
            'auto_recording': 'cloud',
            'join_before_host': True,
            'mute_upon_entry': True,
            'waiting_room': False,
        },
    }

    url = urljoin(ZOOM_API_BASE_URL, 'users/me/meetings')
    response = requests.post(
        url,
        json=payload,
        headers={
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json',
        },
        timeout=10,
    )

    if response.status_code not in (200, 201):
        raise ZoomAPIError(
            f'Failed to create Zoom meeting: {response.status_code}',
            status_code=response.status_code,
            response_data=response.json() if response.content else None,
        )

    data = response.json()
    meeting_id = str(data['id'])
    join_url = data['join_url']

    logger.info(
        'Created Zoom meeting %s for event "%s"',
        meeting_id, event.title,
    )

    return {
        'meeting_id': meeting_id,
        'join_url': join_url,
    }


def validate_webhook_signature(request):
    """Validate an incoming Zoom webhook request signature.

    Zoom webhooks include these headers:
    - x-zm-request-timestamp: Unix timestamp of the request
    - x-zm-signature: v0=HMAC-SHA256 signature

    The signature is computed as:
        HMAC-SHA256(secret, "v0:{timestamp}:{request_body}")

    Args:
        request: Django HttpRequest object.

    Returns:
        bool: True if the signature is valid, False otherwise.
    """
    secret_token = settings.ZOOM_WEBHOOK_SECRET_TOKEN
    if not secret_token:
        logger.warning('ZOOM_WEBHOOK_SECRET_TOKEN not configured')
        return False

    timestamp = request.headers.get('x-zm-request-timestamp', '')
    signature = request.headers.get('x-zm-signature', '')

    if not timestamp or not signature:
        return False

    # Construct the message: v0:{timestamp}:{body}
    body = request.body.decode('utf-8')
    message = f'v0:{timestamp}:{body}'

    # Compute expected signature
    expected_sig = hmac.new(
        secret_token.encode('utf-8'),
        message.encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()
    expected = f'v0={expected_sig}'

    return hmac.compare_digest(expected, signature)
