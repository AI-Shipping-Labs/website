"""Staff-only aggregate outbound email-log API."""

import datetime

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from api.openapi import openapi_spec
from api.safety import error_response
from api.serializers.users import serialize_email_log
from api.utils import require_methods
from api.views.ses_events_list import _parse_offset
from api.views.users import _parse_limit
from email_app.services.email_log_history import (
    DISPOSITIONS,
    apply_email_log_filters,
    email_log_queryset,
)


def _validation_error(field, value, message, *, allowed=None):
    details = {"field": field, "value": value}
    if allowed is not None:
        details["allowed"] = list(allowed)
    return error_response(
        message,
        "validation_error",
        status=422,
        details=details,
    )


def _parse_date(raw, field):
    if raw is None or raw == "":
        return None, None
    try:
        return datetime.date.fromisoformat(raw), None
    except (TypeError, ValueError):
        return None, _validation_error(
            field, raw, f"Invalid YYYY-MM-DD date: {raw!r}",
        )


EMAIL_LOG_ROW_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "integer"},
        "recipient_email": {"type": "string"},
        "user_id": {"type": ["integer", "null"]},
        "user_email": {"type": ["string", "null"]},
        "email_type": {"type": "string"},
        "subject": {"type": "string"},
        "campaign_id": {"type": ["integer", "null"]},
        "campaign_subject": {"type": ["string", "null"]},
        "sent_at": {"type": "string", "format": "date-time"},
        "ses_message_id": {"type": "string"},
        "opened_at": {"type": ["string", "null"]},
        "opens": {"type": "integer"},
        "clicked_at": {"type": ["string", "null"]},
        "clicks": {"type": "integer"},
        "bounced_at": {"type": ["string", "null"]},
        "bounce_type": {"type": "string"},
        "bounce_subtype": {"type": "string"},
        "complained_at": {"type": ["string", "null"]},
        "disposition": {"type": "string", "enum": list(DISPOSITIONS)},
    },
}

EMAIL_LOG_PAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "email_logs": {"type": "array", "items": EMAIL_LOG_ROW_SCHEMA},
        "count": {"type": "integer"},
        "limit": {"type": "integer"},
        "offset": {"type": "integer"},
    },
}

EMAIL_LOG_QUERY_SPEC = {
    "q": {"type": "string", "required": False, "description": "Recipient substring; an exact primary/alias address expands to canonical history."},
    "kind": {"type": "string", "required": False, "description": "Exact stored email_type value."},
    "status": {"type": "string", "required": False, "enum": list(DISPOSITIONS)},
    "since": {"type": "string", "format": "date", "required": False, "description": "Inclusive UTC sent date."},
    "until": {"type": "string", "format": "date", "required": False, "description": "Inclusive UTC sent date."},
    "limit": {"type": "integer", "required": False, "description": "Default 50; clamped to 200."},
    "offset": {"type": "integer", "required": False, "description": "Default 0; must be non-negative."},
}


@token_required
@csrf_exempt
@require_methods("GET")
@openapi_spec(
    tag="Email log",
    summary="List accepted outbound emails",
    methods={
        "GET": {
            "description": (
                "Newest-first SES-accepted sends. Disposition is the exclusive "
                "strongest of sent, delivered, opened, clicked, bounced, and "
                "complained. Email bodies and raw provider payloads are omitted."
            ),
            "query": EMAIL_LOG_QUERY_SPEC,
            "responses": {
                200: {"description": "Matching outbound sends.", "schema": EMAIL_LOG_PAGE_SCHEMA},
                401: {"description": "Missing, invalid, or non-staff token."},
                422: {"description": "Invalid filter or pagination value."},
            },
        },
    },
)
def email_log_list(request):
    limit, error = _parse_limit(request.GET.get("limit"))
    if error is not None:
        return error
    offset, error = _parse_offset(request.GET.get("offset"))
    if error is not None:
        return error
    since, error = _parse_date(request.GET.get("since"), "since")
    if error is not None:
        return error
    until, error = _parse_date(request.GET.get("until"), "until")
    if error is not None:
        return error
    if since is not None and until is not None and since > until:
        return _validation_error(
            "until", request.GET.get("until"), "since must not be after until",
        )

    status = request.GET.get("status", "").strip()
    if status and status not in DISPOSITIONS:
        return _validation_error(
            "status", status, f"Invalid status: {status!r}", allowed=DISPOSITIONS,
        )

    queryset = apply_email_log_filters(
        email_log_queryset(),
        search=request.GET.get("q", ""),
        kind=request.GET.get("kind", ""),
        status=status,
        since=since,
        until=until,
    ).order_by("-sent_at", "-pk")
    count = queryset.count()
    rows = [serialize_email_log(log) for log in queryset[offset:offset + limit]]
    return JsonResponse({
        "email_logs": rows,
        "count": count,
        "limit": limit,
        "offset": offset,
    })
