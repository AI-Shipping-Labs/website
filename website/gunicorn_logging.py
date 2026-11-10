"""Privacy-safe access-log atoms for the Gunicorn web process.

The log keeps only a coarse client network, sanitized query parameters, and
the User-Agent for crawler forensics. It never writes raw client addresses or
known secret values. Recipient, booking, and preview tokens in paths are also
redacted before any access-log atom is returned.

Imported by gunicorn's arbiter before Django is configured, so this
module imports nothing from Django and reads no settings.
"""

from __future__ import annotations

import re
from urllib.parse import unquote_plus

from gunicorn.glogging import Logger  # type: ignore[import-untyped]

from website.request_ip import client_network_for_logging

REDACTED_SEGMENT = '[token]'
TOKEN_PATH_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r'/t/o/[A-Za-z0-9._~%-]+\.gif'), f'/t/o/{REDACTED_SEGMENT}.gif'),
    (re.compile(r'/t/c/[A-Za-z0-9._~%-]+'), f'/t/c/{REDACTED_SEGMENT}'),
    (re.compile(r'/unsubscribe/[A-Za-z0-9._~%-]+'), f'/unsubscribe/{REDACTED_SEGMENT}'),
    (
        re.compile(r'/book/(confirmed|reschedule|cancel|ics)/[A-Za-z0-9._~%-]+'),
        rf'/book/\1/{REDACTED_SEGMENT}',
    ),
    (
        re.compile(
            r'/(blog|marketing-pages|workshops)/preview/'
            r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}'
        ),
        rf'/\1/preview/{REDACTED_SEGMENT}',
    ),
)
SENSITIVE_QUERY_NAME_RE = re.compile(
    r'(?:^|[._\[\]-])(?:token|key|secret|code|sig|signature|email|password|auth)'
    r'(?:$|[._\[\]-])',
    re.IGNORECASE,
)


def _single_line(value: object) -> str:
    """Keep an untrusted log atom on one line and inside its quoted field."""
    return str(value or '-').replace('\\', '\\\\').replace('"', '\\"').replace(
        '\r', ' '
    ).replace('\n', ' ')


def redact_query_string(value: object) -> str:
    """Redact secret-like query values while preserving useful filter values."""
    if not isinstance(value, str) or not value:
        return str(value or '-')
    prefix = '?' if value.startswith('?') else ''
    raw_query = value[1:] if prefix else value
    parts = []
    for part in raw_query.split('&'):
        key, separator, _value = part.partition('=')
        decoded_key = unquote_plus(key).strip()
        if separator and SENSITIVE_QUERY_NAME_RE.search(decoded_key):
            part = f'{key}=[redacted]'
        parts.append(part)
    return _single_line(prefix + '&'.join(parts))


def redact_token_path(value: str) -> str:
    """Replace the token segment of a recipient link."""
    if not isinstance(value, str) or not value:
        return value
    for pattern, replacement in TOKEN_PATH_PATTERNS:
        value = pattern.sub(replacement, value)
    return value


def is_token_bearing_path(path: object) -> bool:
    """Return whether a WSGI path carries a recipient token."""
    if not isinstance(path, str):
        return False
    for pattern, _replacement in TOKEN_PATH_PATTERNS:
        if pattern.search(path):
            return True
    return False


class RecipientTokenSafeLogger(Logger):
    """Gunicorn access logger that never writes a recipient token."""

    def atoms(self, resp, req, environ, request_time):  # type: ignore[no-untyped-def]
        values = super().atoms(resp, req, environ, request_time)
        values['h'] = client_network_for_logging(
            environ.get('REMOTE_ADDR', ''),
            environ.get('HTTP_X_FORWARDED_FOR', ''),
        )
        values['q'] = redact_query_string(values.get('q', ''))
        values['a'] = _single_line(values.get('a', ''))
        if is_token_bearing_path(environ.get('PATH_INFO')):
            return {
                key: redact_token_path(value) if isinstance(value, str) else value
                for key, value in values.items()
            }
        return values
