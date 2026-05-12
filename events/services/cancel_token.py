"""Signed JWT helpers for one-click event-registration cancellation.

Mirrors the JWT pattern used by ``accounts.views.auth._generate_verification_token``
and ``email_app.views.newsletter._generate_unsubscribe_token``: HS256 signed
against ``settings.SECRET_KEY`` with a typed ``action`` claim.

The token authorizes a single, scoped action: cancel one specific
``EventRegistration`` row. The view that consumes it must re-validate
that ``registration_id``, ``event_id`` and ``user_id`` all still match a
live row, so a deleted-and-recreated registration cannot be cancelled
with a stale token.
"""

import datetime

import jwt
from django.conf import settings

JWT_ALGORITHM = "HS256"
CANCEL_ACTION = "cancel_event_registration"
CANCEL_TOKEN_EXPIRY_DAYS = 30


class CancelTokenError(Exception):
    """Base class for cancel-token decoding failures."""


class CancelTokenInvalid(CancelTokenError):
    """Token is malformed, signed with the wrong key, or has the wrong action claim."""


class CancelTokenExpired(CancelTokenError):
    """Token decoded successfully but its ``exp`` is in the past."""


def generate_cancel_token(registration, expiry_days=CANCEL_TOKEN_EXPIRY_DAYS):
    """Encode a JWT that authorizes cancelling ``registration``.

    Args:
        registration: ``EventRegistration`` instance.
        expiry_days: Days until the token's ``exp`` claim (default 30).

    Returns:
        str: encoded JWT string suitable for a URL ``?token=`` parameter.
    """
    payload = {
        "registration_id": registration.id,
        "event_id": registration.event_id,
        "user_id": registration.user_id,
        "action": CANCEL_ACTION,
        "exp": datetime.datetime.now(datetime.timezone.utc)
        + datetime.timedelta(days=expiry_days),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=JWT_ALGORITHM)


def decode_cancel_token(token):
    """Decode and validate a cancel token.

    Args:
        token: encoded JWT string.

    Returns:
        dict: decoded payload with ``registration_id``, ``event_id``,
        ``user_id``, ``action``, and ``exp``.

    Raises:
        CancelTokenExpired: ``exp`` is in the past.
        CancelTokenInvalid: signature mismatch, malformed token, or the
            ``action`` claim is not ``cancel_event_registration``.
    """
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[JWT_ALGORITHM],
        )
    except jwt.ExpiredSignatureError as exc:
        raise CancelTokenExpired(str(exc)) from exc
    except jwt.InvalidTokenError as exc:
        raise CancelTokenInvalid(str(exc)) from exc

    if payload.get("action") != CANCEL_ACTION:
        raise CancelTokenInvalid("Wrong action claim")

    for claim in ("registration_id", "event_id", "user_id"):
        if claim not in payload:
            raise CancelTokenInvalid(f"Missing {claim} claim")

    return payload
