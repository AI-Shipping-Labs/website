"""Access-log redaction for paths that carry a recipient token.

Mirrors the website canonical policy. The deployed access format keeps
only the path (no query string, client address, or headers), but Relay
recipient links put an opaque per-recipient token in the path itself, so
this logger redacts that one segment before it is written.

Imported by gunicorn's arbiter before Django is configured, so this
module imports nothing from Django and reads no settings.
"""

from __future__ import annotations

import re

from gunicorn.glogging import Logger  # type: ignore[import-untyped]

REDACTED_SEGMENT = '[token]'
TOKEN_PATH_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r'/t/o/[A-Za-z0-9._~%-]+\.gif'), f'/t/o/{REDACTED_SEGMENT}.gif'),
    (re.compile(r'/t/c/[A-Za-z0-9._~%-]+'), f'/t/c/{REDACTED_SEGMENT}'),
    (re.compile(r'/unsubscribe/[A-Za-z0-9._~%-]+'), f'/unsubscribe/{REDACTED_SEGMENT}'),
)


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
        if not is_token_bearing_path(environ.get('PATH_INFO')):
            return values
        redacted = {}
        for key, value in values.items():
            if isinstance(value, str):
                redacted[key] = redact_token_path(value)
            else:
                redacted[key] = value
        return redacted
