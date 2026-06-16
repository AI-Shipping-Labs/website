"""Shared JWT helpers for first-party user action links."""

import datetime

import jwt
from django.conf import settings

JWT_ALGORITHM = "HS256"
ALLOWED_EXTRA_PAYLOAD_FIELDS = {"redirect_to"}
RESERVED_PAYLOAD_FIELDS = {"user_id", "action", "exp"}


def generate_user_action_token(user_id, action, *, expiry_hours=None, **extra_payload):
    """Generate an HS256 JWT for first-party user action links."""
    reserved_fields = RESERVED_PAYLOAD_FIELDS.intersection(extra_payload)
    if reserved_fields:
        raise ValueError(
            "Extra payload contains reserved field(s): "
            + ", ".join(sorted(reserved_fields))
        )

    unsupported_fields = set(extra_payload) - ALLOWED_EXTRA_PAYLOAD_FIELDS
    if unsupported_fields:
        raise ValueError(
            "Unsupported extra payload field(s): "
            + ", ".join(sorted(unsupported_fields))
        )

    payload = {
        "user_id": user_id,
        "action": action,
    }
    if expiry_hours is not None:
        payload["exp"] = datetime.datetime.now(datetime.timezone.utc) + (
            datetime.timedelta(hours=expiry_hours)
        )
    payload.update({key: value for key, value in extra_payload.items() if value})
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=JWT_ALGORITHM)
