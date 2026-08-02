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
import re
import time
from urllib.parse import urljoin
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests

from integrations.config import get_config

logger = logging.getLogger(__name__)

# Token cache (module-level for simplicity; in production use Redis/cache)
_token_cache = {
    'access_token': None,
    'expires_at': 0,
}

ZOOM_OAUTH_TOKEN_URL = 'https://zoom.us/oauth/token'
ZOOM_API_BASE_URL = 'https://api.zoom.us/v2/'
ZOOM_PROVIDER_MESSAGE_MAX_LENGTH = 300

_URL_RE = re.compile(r'https?://\S+', re.IGNORECASE)
_BEARER_RE = re.compile(r'\bBearer\s+\S+', re.IGNORECASE)
_SENSITIVE_VALUE_RE = re.compile(
    r'(?i)(?P<quote>["\']?)(?P<key>access[_ -]?token|refresh[_ -]?token|'
    r'client[_ -]?secret|authorization|join[_ -]?url|password)'
    r'(?P=quote)\s*[:=]\s*'
    r'(?P<value>"[^"]*"|\'[^\']*\'|[^\s,;]+)',
)
_STRUCTURED_MESSAGE = 'Structured provider message omitted.'


def _bounded_text(text, max_length):
    if max_length <= 0:
        return None
    if len(text) > max_length:
        if max_length == 1:
            return '…'
        return text[: max_length - 1].rstrip() + '…'
    return text


def sanitize_provider_message(value, *, max_length=ZOOM_PROVIDER_MESSAGE_MAX_LENGTH):
    """Return bounded provider text with credentials and URLs redacted.

    Only Zoom's explicit ``message`` value is passed here; callers never
    stringify or expose the full provider response object. Non-string message
    values are replaced with a generic marker: arbitrary structured content is
    never serialized into diagnostics, logs, exception state, or API output.
    """
    if value is None:
        return None
    if isinstance(value, (dict, list, tuple, set)):
        return _bounded_text(_STRUCTURED_MESSAGE, max_length)
    if isinstance(value, bytes):
        text = value.decode('utf-8', errors='replace')
    elif isinstance(value, (str, int, float, bool)):
        text = str(value)
    else:
        return _bounded_text(_STRUCTURED_MESSAGE, max_length)

    # A provider can put a JSON-encoded object/array inside its nominal string
    # ``message`` field. Treat that exactly like an actual structured value so
    # quoted/nested sensitive and unrelated fields cannot bypass the allowlist.
    stripped = text.strip()
    if stripped.startswith(('{', '[')):
        try:
            decoded = json.loads(stripped)
        except (TypeError, ValueError):
            pass
        else:
            if isinstance(decoded, (dict, list)):
                return _bounded_text(_STRUCTURED_MESSAGE, max_length)

    text = ' '.join(text.split())
    text = _BEARER_RE.sub('Bearer [redacted]', text)
    text = _SENSITIVE_VALUE_RE.sub(
        lambda match: f'{match.group("key")}=[redacted]',
        text,
    )
    text = _URL_RE.sub('[redacted-url]', text)
    return _bounded_text(text, max_length) or None


def _provider_code(value):
    """Return a small scalar provider code, never an arbitrary payload."""
    if value is None or isinstance(value, (dict, list, tuple, set)):
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    return sanitize_provider_message(value, max_length=64)


def _response_data(response):
    """Best-effort JSON decoding for extracting only known error fields."""
    try:
        data = response.json()
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


class ZoomAPIError(Exception):
    """Raised when the Zoom API returns an error."""

    def __init__(self, message, status_code=None, response_data=None):
        self.status_code = status_code
        # Retain only the two provider fields that are safe and useful for
        # operator diagnosis. Never keep a raw response payload on the error.
        response_data = response_data if isinstance(response_data, dict) else {}
        self.provider_code = _provider_code(response_data.get('code'))
        self.provider_message = sanitize_provider_message(
            response_data.get('message'),
        )
        self.response_data = {
            key: value
            for key, value in (
                ('code', self.provider_code),
                ('message', self.provider_message),
            )
            if value is not None
        }
        super().__init__(sanitize_provider_message(message) or 'Zoom request failed.')

    def diagnostics(self, operation):
        """Return the bounded, JSON-safe operator diagnostic fields."""
        details = {'operation': operation}
        if self.status_code is not None:
            details['http_status'] = self.status_code
        if self.provider_code is not None:
            details['provider_code'] = self.provider_code
        if self.provider_message is not None:
            details['provider_message'] = self.provider_message
        return details


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

    client_id = get_config('ZOOM_CLIENT_ID')
    client_secret = get_config('ZOOM_CLIENT_SECRET')
    account_id = get_config('ZOOM_ACCOUNT_ID')

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
            response_data=_response_data(response),
        )

    data = response.json()
    _token_cache['access_token'] = data['access_token']
    _token_cache['expires_at'] = now + data.get('expires_in', 3600)

    return _token_cache['access_token']


def clear_token_cache():
    """Clear the cached access token (useful for testing)."""
    _token_cache['access_token'] = None
    _token_cache['expires_at'] = 0


def _config_bool(key, default):
    """Resolve a boolean config key, honouring a per-key default.

    ``is_enabled`` reads ``get_config(key, 'false')`` and so cannot express a
    default of ``true`` for an unset key. We resolve the raw string here with
    the registry default and coerce it ourselves so ``ZOOM_WAITING_ROOM``
    defaults to ``true`` and ``ZOOM_JOIN_BEFORE_HOST`` defaults to ``false``
    when nothing is set in the DB / env.
    """
    val = get_config(key, 'true' if default else 'false')
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in ('true', '1', 'yes')


def _meeting_settings():
    """Build the shared Zoom meeting ``settings`` payload.

    Used by both ``create_meeting`` and ``update_meeting_settings`` so a new
    meeting and an in-place patch always carry the same auto-record /
    join-before-host / waiting-room configuration. ``auto_recording`` defaults
    to ``cloud`` (configurable via ``ZOOM_AUTO_RECORDING`` — ``cloud`` / ``local``
    / ``none``) so event meetings record automatically once the host joins; this
    only takes effect if cloud recording is enabled/licensed at the Zoom account
    level (#1081). ``join_before_host`` defaults OFF so early joiners see Zoom's
    "waiting for the host to start" hold and cloud recording only begins once the
    host joins — with no manual admitting. ``waiting_room`` defaults OFF (it
    would require the host to admit each attendee); it stays configurable for
    operators who want it (issue #1004).
    """
    return {
        'auto_recording': get_config('ZOOM_AUTO_RECORDING', 'cloud'),
        'join_before_host': _config_bool('ZOOM_JOIN_BEFORE_HOST', default=False),
        'mute_upon_entry': True,
        'waiting_room': _config_bool('ZOOM_WAITING_ROOM', default=False),
    }


def build_meeting_payload(event, *, include_type=True):
    """Build the shared Zoom meeting schedule/settings payload for an event."""
    duration = 60
    if event.end_datetime and event.start_datetime:
        delta = event.end_datetime - event.start_datetime
        duration = max(1, int(delta.total_seconds() / 60))

    meeting_timezone = (event.timezone or '').strip()
    try:
        local_start = event.start_datetime.astimezone(ZoneInfo(meeting_timezone))
    except (ZoneInfoNotFoundError, ValueError):
        meeting_timezone = 'UTC'
        local_start = event.start_datetime.astimezone(ZoneInfo('UTC'))

    payload = {
        'topic': event.title,
        'start_time': local_start.strftime('%Y-%m-%dT%H:%M:%S'),
        'duration': duration,
        'timezone': meeting_timezone,
        'settings': _meeting_settings(),
    }
    if include_type:
        payload['type'] = 2  # Scheduled meeting
    return payload


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

    # Zoom interprets a ``start_time`` without a ``Z``/offset as LOCAL time in
    # the supplied ``timezone``. ``build_meeting_payload`` converts stored UTC
    # datetimes to the event timezone before formatting (#996).
    payload = build_meeting_payload(event)

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

    if not 200 <= response.status_code < 300:
        raise ZoomAPIError(
            f'Failed to create Zoom meeting: {response.status_code}',
            status_code=response.status_code,
            response_data=_response_data(response),
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


def update_meeting(event):
    """Patch an existing Zoom meeting's schedule/settings in place.

    A successful PATCH returns HTTP 204 with no body.

    Args:
        event: Event model instance with a non-empty ``zoom_meeting_id``.

    Raises:
        ZoomAPIError: If the Zoom API call fails (non-2xx response).
    """
    token = get_access_token()

    url = urljoin(ZOOM_API_BASE_URL, f'meetings/{event.zoom_meeting_id}')
    response = requests.patch(
        url,
        json=build_meeting_payload(event, include_type=False),
        headers={
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json',
        },
        timeout=10,
    )

    # PATCH returns 204 No Content on success; accept any 2xx for safety.
    if not 200 <= response.status_code < 300:
        raise ZoomAPIError(
            f'Failed to update Zoom meeting: {response.status_code}',
            status_code=response.status_code,
            response_data=_response_data(response),
        )

    logger.info(
        'Updated Zoom meeting %s for event "%s"',
        event.zoom_meeting_id, event.title,
    )


def update_meeting_settings(event):
    """Patch an existing Zoom meeting's settings in place (issue #1004).

    Sends ``PATCH /v2/meetings/{meeting_id}`` with a body of ONLY
    ``{'settings': _meeting_settings()}`` so the waiting-room / join-before-host
    configuration can be applied independently of event schedule edits.
    """
    token = get_access_token()

    url = urljoin(ZOOM_API_BASE_URL, f'meetings/{event.zoom_meeting_id}')
    response = requests.patch(
        url,
        json={'settings': _meeting_settings()},
        headers={
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json',
        },
        timeout=10,
    )

    # PATCH returns 204 No Content on success; accept any 2xx for safety.
    if not 200 <= response.status_code < 300:
        raise ZoomAPIError(
            f'Failed to update Zoom meeting settings: {response.status_code}',
            status_code=response.status_code,
            response_data=_response_data(response),
        )

    logger.info(
        'Updated Zoom meeting %s settings for event "%s"',
        event.zoom_meeting_id, event.title,
    )


def delete_meeting(event):
    """Delete an existing Zoom meeting.

    Zoom returns 204 on successful deletion. A 404 is also treated as success
    because the external meeting is already absent and local cleanup may
    proceed.
    """
    token = get_access_token()

    url = urljoin(ZOOM_API_BASE_URL, f'meetings/{event.zoom_meeting_id}')
    response = requests.delete(
        url,
        headers={
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json',
        },
        timeout=10,
    )

    if not (200 <= response.status_code < 300 or response.status_code == 404):
        raise ZoomAPIError(
            f'Failed to delete Zoom meeting: {response.status_code}',
            status_code=response.status_code,
            response_data=_response_data(response),
        )

    logger.info(
        'Deleted Zoom meeting %s for event "%s" (status %s)',
        event.zoom_meeting_id, event.title, response.status_code,
    )


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
    secret_token = get_config('ZOOM_WEBHOOK_SECRET_TOKEN')
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


def build_url_validation_encrypted_token(plain_token):
    """Return Zoom's URL-validation HMAC using the resolved webhook secret."""
    secret_token = get_config('ZOOM_WEBHOOK_SECRET_TOKEN')
    if not secret_token:
        logger.warning('ZOOM_WEBHOOK_SECRET_TOKEN not configured')
        return ''

    return hmac.new(
        secret_token.encode('utf-8'),
        plain_token.encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()
