"""Operator API to manage email aliases (issue #840a).

Staff-token endpoints to add/remove email aliases that route a billing or
relay email to a canonical ``User`` account, so future Stripe webhooks
resolve correctly (see ``payments.services.webhook_handlers`` and
``accounts.services.email_resolution``).

- ``POST   /api/users/<email>/aliases``                -- add an alias.
- ``DELETE /api/users/<email>/aliases/<alias_email>``  -- remove an alias.

The ``aliases`` list also rides on ``GET /api/users/<email>`` (see
``api.serializers.users.serialize_user_state``) so operators can see
routing at a glance.

Conventions mirror ``api/views/users.py`` / ``api/views/tier_overrides.py``:
``token_required`` (staff-only JSON 401), ``error_response`` envelope,
``@openapi_spec`` coverage, ``transaction.atomic`` around the write + audit
insert, and one ``CommunityAuditLog`` row per write attributed to the token
via ``_actor_label`` -- including idempotent no-ops (the operator's attempt
is itself auditable).
"""

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from accounts.models import EmailAlias
from accounts.services.email_resolution import normalize_email
from api.openapi import openapi_spec
from api.safety import error_response
from api.utils import parse_json_body, require_methods
from community.models import CommunityAuditLog

User = get_user_model()


def _actor_label(request):
    """Short label identifying the API caller (mirrors ``api/views/users.py``)."""
    token = getattr(request, "auth_token", None)
    if token is None:
        return "unknown"
    if token.name:
        return token.name
    return token.key_prefix


def _find_user(email):
    if not email:
        return None
    return User.objects.filter(email__iexact=email).first()


def _user_not_found_response():
    return error_response("User not found", "user_not_found", status=404)


def _is_valid_email(value):
    """Per-email validity: non-empty string, contains ``@``, passes validator."""
    if not isinstance(value, str):
        return False
    value = value.strip()
    if not value or "@" not in value:
        return False
    try:
        validate_email(value)
    except ValidationError:
        return False
    return True


def _serialize_aliases(user):
    """Return the user's alias emails sorted, matching the user-read shape."""
    return list(
        user.email_aliases.order_by("email").values_list("email", flat=True)
    )


_ALIAS_ADD_ALLOWED_FIELDS = {"alias_email", "note"}


@token_required
@csrf_exempt
@require_methods("POST")
@openapi_spec(
    tag="Users",
    summary="Add an email alias to a user",
    methods={
        "POST": {
            "summary": "Add an email alias (routes future Stripe events)",
            "description": (
                "Adds an ``EmailAlias`` routing ``alias_email`` -> the user "
                "identified by ``<email>``. Future Stripe webhooks whose "
                "billing email is the alias resolve to this canonical account "
                "instead of creating a duplicate. Idempotent: re-adding the "
                "same alias to the same owner returns 200, creates no "
                "duplicate row, and still writes an audit row. 409 "
                "``alias_is_primary_email`` if the alias is already an existing "
                "primary ``User.email``; 409 ``alias_taken`` if it already "
                "routes to a DIFFERENT user; 422 on malformed email; 404 when "
                "``<email>`` is unknown."
            ),
            "request_body": {
                "required": ["alias_email"],
                "properties": {
                    "alias_email": {"type": "string"},
                    "note": {"type": "string"},
                },
                "example": {
                    "alias_email": "47-gentle.virtual@icloud.com",
                    "note": "Apple Pay relay for stefano",
                },
            },
            "responses": {
                200: {
                    "description": "Owner email + current alias list.",
                    "example": {
                        "email": "stefano@example.com",
                        "aliases": ["47-gentle.virtual@icloud.com"],
                    },
                },
                404: {
                    "description": "Unknown owner email.",
                    "example": {
                        "error": "User not found",
                        "code": "user_not_found",
                    },
                },
                409: {
                    "description": (
                        "Alias collides with a primary email "
                        "(``alias_is_primary_email``) or another user's alias "
                        "(``alias_taken``)."
                    ),
                    "example": {
                        "error": "Alias email is already a primary account email",
                        "code": "alias_is_primary_email",
                    },
                },
                422: {
                    "description": "Malformed alias email.",
                    "example": {
                        "error": "alias_email is not a valid email",
                        "code": "invalid_email",
                        "details": {"field": "alias_email"},
                    },
                },
            },
        },
    },
)
def user_aliases_add(request, email):
    """``POST /api/users/<email>/aliases`` -- add an alias (idempotent)."""
    user = _find_user(email)
    if user is None:
        return _user_not_found_response()

    data, parse_error = parse_json_body(request)
    if parse_error is not None:
        return parse_error
    if not isinstance(data, dict):
        return error_response(
            "Body must be a JSON object",
            "invalid_type",
            details={"field": "body", "expected": "object"},
        )

    for key in data:
        if key not in _ALIAS_ADD_ALLOWED_FIELDS:
            return error_response(
                f"Unknown field: {key}",
                "unknown_field",
                status=422,
                details={"field": key},
            )

    raw_alias = data.get("alias_email")
    if not _is_valid_email(raw_alias):
        return error_response(
            "alias_email is not a valid email",
            "invalid_email",
            status=422,
            details={"field": "alias_email"},
        )

    normalized = normalize_email(raw_alias)
    note = data.get("note")
    note = "" if note is None else str(note)
    actor = _actor_label(request)

    with transaction.atomic():
        # Collision guard 1: the alias must not be an existing primary login.
        # An address is either a primary login OR an alias, never both.
        if User.objects.filter(email__iexact=normalized).exists():
            return error_response(
                "Alias email is already a primary account email",
                "alias_is_primary_email",
                status=409,
            )

        existing = EmailAlias.objects.select_related("user").filter(
            email=normalized
        ).first()
        if existing is not None and existing.user_id != user.pk:
            # Collision guard 2: already routes to a DIFFERENT account.
            return error_response(
                "Alias email already routes to a different account",
                "alias_taken",
                status=409,
            )

        if existing is None:
            EmailAlias.objects.create(
                user=user,
                email=normalized,
                source=EmailAlias.SOURCE_MANUAL,
                note=note,
                created_by=request.user,
            )
            verb = "added"
        else:
            # Idempotent re-add to the same owner: no duplicate row, still
            # audited so the operator's attempt is recorded.
            verb = "no-op add"

        CommunityAuditLog.objects.create(
            user=user,
            action="email_alias_added",
            details=f"{verb} alias {normalized!r}; actor_token={actor}",
        )
        aliases = _serialize_aliases(user)

    return JsonResponse({"email": user.email, "aliases": aliases}, status=200)


@token_required
@csrf_exempt
@require_methods("DELETE")
@openapi_spec(
    tag="Users",
    summary="Remove an email alias from a user",
    methods={
        "DELETE": {
            "summary": "Remove an email alias (idempotent)",
            "description": (
                "Removes the ``EmailAlias`` routing ``<alias_email>`` to the "
                "user identified by ``<email>``. Idempotent: removing an alias "
                "that does not exist (or is not owned by this user) still "
                "returns 200 and writes an audit row. 404 when ``<email>`` is "
                "unknown."
            ),
            "responses": {
                200: {
                    "description": "Owner email + remaining alias list.",
                    "example": {
                        "email": "stefano@example.com",
                        "aliases": [],
                    },
                },
                404: {
                    "description": "Unknown owner email.",
                    "example": {
                        "error": "User not found",
                        "code": "user_not_found",
                    },
                },
            },
        },
    },
)
def user_aliases_remove(request, email, alias_email):
    """``DELETE /api/users/<email>/aliases/<alias_email>`` -- remove (idempotent)."""
    user = _find_user(email)
    if user is None:
        return _user_not_found_response()

    normalized = normalize_email(alias_email)
    actor = _actor_label(request)

    with transaction.atomic():
        # Only remove an alias owned by THIS user. An alias routing to a
        # different account is left untouched (the DELETE is scoped to the
        # owner in the path), but the attempt is still recorded as a no-op.
        deleted, _ = EmailAlias.objects.filter(
            user=user, email=normalized
        ).delete()
        verb = "removed" if deleted else "no-op remove"
        CommunityAuditLog.objects.create(
            user=user,
            action="email_alias_removed",
            details=f"{verb} alias {normalized!r}; actor_token={actor}",
        )
        aliases = _serialize_aliases(user)

    return JsonResponse({"email": user.email, "aliases": aliases}, status=200)
