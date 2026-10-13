"""CRM calls endpoint (issue #1949).

Staff-token-gated write surface over the CRM call log:

``POST /api/crm/calls`` records one call through
:func:`crm.services.calls.record_call`, the single write path shared
with the Studio form and the #1948 booking app. The ``email`` body
field resolves to a user case-insensitively inside the service; an
unknown email still records the call with ``contact=NULL`` and the raw
``email`` snapshot. A non-blank ``source_ref`` upserts on
``(source, source_ref)`` so booking retries never duplicate.

``@token_required`` (staff-owned tokens only; non-staff tokens already
401 via ``accounts.auth``), ``@require_methods("POST")`` (any other
method -> 405).
"""

import json

from django.http import JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from api.openapi import openapi_spec
from api.safety import error_response
from api.serializers.datetime import isoformat_or_none
from api.utils import require_methods
from crm.models.call import CALL_SOURCE_CHOICES
from crm.services.calls import record_call

_VALID_SOURCES = [choice for choice, _label in CALL_SOURCE_CHOICES]


def _parse_iso_datetime(raw):
    """Parse an ISO 8601 body value into an aware datetime, or None."""
    parsed = parse_datetime(str(raw).strip())
    if parsed is None:
        return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed)
    return parsed


def _serialize_call(call):
    """Model-field-only serializer; safe for unmatched-email rows."""
    return {
        "id": call.pk,
        "email": call.email,
        "contact_id": call.contact_id,
        "occurred_at": isoformat_or_none(call.occurred_at),
        "summary": call.summary,
        "source": call.source,
        "source_ref": call.source_ref,
        "created_by_email": (
            call.created_by.email if call.created_by_id else None
        ),
        "created_at": isoformat_or_none(call.created_at),
        "updated_at": isoformat_or_none(call.updated_at),
    }


def _validation_error(field, message):
    return error_response(
        message,
        "validation_error",
        status=422,
        details={"field": field},
    )


def _invalid_source_response(source):
    return error_response(
        f"Invalid source: {source!r}",
        "validation_error",
        status=422,
        details={
            "field": "source",
            "value": source,
            "allowed": _VALID_SOURCES,
        },
    )


@token_required
@csrf_exempt
@require_methods("POST")
@openapi_spec(
    tag="CRM",
    summary="Record a logged call",
    methods={
        "POST": {
            "summary": "Record a logged CRM call",
            "description": (
                "Creates (or, with ``source_ref``, upserts) one CRM call "
                "for an email: when the call was (``occurred_at``, ISO "
                "8601), what was discussed (``summary``), and where the "
                "row came from (``source``, default ``manual``; the "
                "booking app passes ``booking`` plus its confirmation id "
                "as ``source_ref``). The email resolves to a user "
                "case-insensitively; an unmatched email still records "
                "the call with the raw email snapshot and a null "
                "``contact_id``. Delegates to "
                "``crm.services.calls.record_call`` -- the same write "
                "path as the Studio form. Staff-token only."
            ),
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "required": ["email", "occurred_at"],
                            "properties": {
                                "email": {"type": "string"},
                                "occurred_at": {"type": "string"},
                                "summary": {"type": "string"},
                                "source": {
                                    "type": "string",
                                    "enum": _VALID_SOURCES,
                                },
                                "source_ref": {"type": "string"},
                            },
                        },
                    },
                },
            },
            "responses": {
                201: {"description": "Call recorded (created or upserted)."},
                401: {
                    "description": "Missing, invalid, or non-staff token.",
                },
                422: {
                    "description": (
                        "Missing email, unparseable ``occurred_at``, or "
                        "invalid ``source``."
                    ),
                },
                405: {"description": "Only POST is supported."},
            },
        },
    },
)
def crm_calls(request):
    """``POST /api/crm/calls`` -- record one call via ``record_call``."""
    try:
        payload = json.loads(request.body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return error_response(
            "Request body must be valid JSON",
            "validation_error",
            status=422,
            details={"field": "body"},
        )
    if not isinstance(payload, dict):
        return error_response(
            "Request body must be a JSON object",
            "validation_error",
            status=422,
            details={"field": "body"},
        )

    email = (payload.get("email") or "").strip()
    if not email:
        return _validation_error("email", "email is required")

    occurred_at = None
    raw_occurred = payload.get("occurred_at")
    if raw_occurred:
        occurred_at = _parse_iso_datetime(raw_occurred)
    if occurred_at is None:
        return _validation_error(
            "occurred_at", "occurred_at must be an ISO 8601 datetime",
        )

    source = (payload.get("source") or "").strip() or "manual"
    if source not in _VALID_SOURCES:
        return _invalid_source_response(source)

    # Email resolution happens inside the service (issue #1949 AC4);
    # the endpoint stays a thin, validating wrapper.
    call = record_call(
        email=email,
        occurred_at=occurred_at,
        summary=payload.get("summary") or "",
        source=source,
        source_ref=(payload.get("source_ref") or "").strip(),
        created_by=request.user,
    )
    return JsonResponse(_serialize_call(call), status=201)
