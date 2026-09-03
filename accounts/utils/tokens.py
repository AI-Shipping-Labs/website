"""Shared JWT helpers for first-party user action links."""

import datetime

import jwt
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.utils.crypto import constant_time_compare
from django.utils.http import base36_to_int

JWT_ALGORITHM = "HS256"
ALLOWED_EXTRA_PAYLOAD_FIELDS = {"redirect_to", "return_path"}
RESERVED_PAYLOAD_FIELDS = {"user_id", "action", "exp"}
PASSWORD_RESET_ACTION = "password_reset"
PASSWORD_RESET_PROOF_CLAIM = "reset_proof"


class PasswordResetTokenError(Exception):
    """Structured password-reset token failure for GET/POST mapping."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


class PasswordStateResetTokenGenerator(PasswordResetTokenGenerator):
    """HMAC proof bound to the user's current password hash, not last_login.

    Issuer-specific lifetime lives on the wrapping JWT ``exp`` claim. Django's
    global ``PASSWORD_RESET_TIMEOUT`` is ignored so a 24-hour Maven welcome
    link is not clipped by that setting.
    """

    key_salt = "accounts.utils.tokens.PasswordStateResetTokenGenerator"

    def _make_hash_value(self, user, timestamp):
        return f"{user.pk}{user.password}{timestamp}"

    def check_token(self, user, token):
        if not (user and token):
            return False
        try:
            ts_b36, _ = token.split("-")
        except ValueError:
            return False
        try:
            ts = base36_to_int(ts_b36)
        except ValueError:
            return False
        for secret in [self.secret, *self.secret_fallbacks]:
            if constant_time_compare(
                self._make_token_with_timestamp(user, ts, secret),
                token,
            ):
                return True
        return False


_PASSWORD_RESET_PROOF_GENERATOR = PasswordStateResetTokenGenerator()


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


def generate_password_reset_token(user, *, expiry_hours=1):
    """Issue a password-reset JWT wrapping a password-state HMAC proof."""
    payload = {
        "user_id": user.pk,
        "action": PASSWORD_RESET_ACTION,
        "exp": datetime.datetime.now(datetime.timezone.utc)
        + datetime.timedelta(hours=expiry_hours),
        PASSWORD_RESET_PROOF_CLAIM: _PASSWORD_RESET_PROOF_GENERATOR.make_token(user),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=JWT_ALGORITHM)


def load_password_reset_payload(token):
    """Decode a reset JWT and require ``action=password_reset``.

    Does not look up the user or check the password-state proof.
    """
    try:
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[JWT_ALGORITHM]
        )
    except jwt.ExpiredSignatureError as exc:
        raise PasswordResetTokenError("expired") from exc
    except jwt.InvalidTokenError as exc:
        raise PasswordResetTokenError("invalid") from exc

    if payload.get("action") != PASSWORD_RESET_ACTION:
        raise PasswordResetTokenError("wrong_action")
    if payload.get("user_id") is None:
        raise PasswordResetTokenError("invalid")
    return payload


def password_reset_proof_matches(user, payload):
    """Return whether ``payload`` proves ``user``'s current password state."""
    proof = payload.get(PASSWORD_RESET_PROOF_CLAIM)
    if not isinstance(proof, str) or not proof:
        return False
    return _PASSWORD_RESET_PROOF_GENERATOR.check_token(user, proof)


def resolve_password_reset_token(token):
    """Validate expiry, action, user, and password-state proof."""
    payload = load_password_reset_payload(token)
    UserModel = get_user_model()
    try:
        user = UserModel.objects.get(pk=payload["user_id"])
    except UserModel.DoesNotExist as exc:
        raise PasswordResetTokenError("user_not_found") from exc
    if not password_reset_proof_matches(user, payload):
        raise PasswordResetTokenError("invalid")
    return user, payload
