"""Device-login credential minter for the shared device flow.

The package calls ``mint_device_token(*, user, label, request)`` after an
admin approves a grant in the browser. It mints a full-power staff
``accounts.Token`` (the same credential the CLI used to copy by hand);
``Token.save()`` also mirrors it into ``community_base.api`` so the new
credential works against both the legacy ``/api`` (``Token`` scheme) and
the package ``/api/v1`` (``Bearer``) surfaces.
"""

from __future__ import annotations

from accounts.models.token import Token


def mint_device_token(*, user, label: str, request) -> dict:
    name = f"Device login {label}".strip()[:100] or "Device login"
    _, plaintext = Token.create_for_user(user=user, name=name)
    full_name = ""
    try:
        full_name = user.get_full_name() or ""
    except Exception:
        full_name = ""
    return {
        "token": plaintext,
        "token_id": None,
        "expires_at": None,
        "user": {"email": getattr(user, "email", ""), "name": full_name},
    }
