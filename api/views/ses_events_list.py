"""Aggregate SES events list endpoint (issue #829).

``GET /api/ses-events`` -- a read-only collection over *all* ``SesEvent``
rows, across every recipient, including events whose ``user`` FK is
``None`` (lead-magnet / newsletter-only addresses that never became a
``User``). The per-user endpoint ``GET /api/users/<email>/ses-events``
(``api/views/users.py``) filters ``SesEvent.objects.filter(user=user)``
and therefore structurally misses those rows; this endpoint exists so an
operator can answer "how many bounces did prod capture for this campaign
/ in this window?".

Routing: Django binds one view per ``path()`` and the canonical
``ses-events`` route already points at the POST-only SNS webhook
(``api/views/ses_events.py``). To keep the reporter's requested
``GET /api/ses-events`` path without a second route, ``ses_events_dispatch``
is a thin method dispatcher registered at that path: ``GET`` is served by
the aggregate list view (token-gated), every other method falls through
to the unchanged webhook (signature/shared-secret-gated, its own
``@require_http_methods(["POST"])`` still emits the 405 for anything
else). No new public surface: GET is ``@token_required`` (staff-owned
tokens only), POST is signature-gated.

Auth: ``@token_required`` returns JSON 401 for a missing/invalid token
AND for a valid token whose owner is not staff
(``accounts/auth.py``). This endpoint exposes recipient emails plus
diagnostic codes and MUST NOT be public.
"""

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from api.openapi import openapi_spec
from api.openapi.decorator import OPENAPI_SPEC_ATTR
from api.safety import error_response
from api.serializers.users import serialize_ses_event
from api.views.ses_events import ses_events as _ses_events_webhook
from api.views.users import (
    _SES_EVENT_EXAMPLE,
    VALID_SES_EVENT_TYPES,
    _parse_limit,
    _parse_since,
)
from email_app.models import SesEvent

# Convenience value for ``type``: not a model choice, but expands to the
# three concrete bounce event types so an operator can ask "all bounces"
# without enumerating subtypes.
_BOUNCE_EVENT_TYPES = (
    SesEvent.EVENT_TYPE_BOUNCE_PERMANENT,
    SesEvent.EVENT_TYPE_BOUNCE_TRANSIENT,
    SesEvent.EVENT_TYPE_BOUNCE_OTHER,
)
_BOUNCE_ALIAS = "bounce"


def _parse_offset(raw, *, field="offset"):
    """Parse the ``offset`` query param into a non-negative int.

    Mirrors the 422 ``validation_error`` shape of ``_parse_limit`` but
    allows zero (an offset of 0 is the first page) and rejects negatives.
    """
    if raw is None or raw == "":
        return 0, None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None, error_response(
            f"Invalid integer: {raw!r}",
            "validation_error",
            status=422,
            details={"field": field, "value": raw},
        )
    if value < 0:
        return None, error_response(
            f"{field} must be a non-negative integer",
            "validation_error",
            status=422,
            details={"field": field, "value": raw},
        )
    return value, None


def _parse_email_log(raw, *, field="email_log"):
    """Parse the ``email_log`` query param into an int ``email_log_id``.

    Any non-integer value is a 422 ``validation_error`` (there is no
    min-1 floor -- callers paste whatever id Studio shows them).
    """
    if raw is None or raw == "":
        return None, None
    try:
        return int(raw), None
    except (TypeError, ValueError):
        return None, error_response(
            f"Invalid integer: {raw!r}",
            "validation_error",
            status=422,
            details={"field": field, "value": raw},
        )


# The full OpenAPI operation for the aggregate GET list. Defined as a
# module constant so ``ses_events_dispatch`` (the route-bound view the
# builder actually reads) can compose it with the webhook's POST spec
# into a single rich path-item -- the builder reads metadata off the
# route-bound view only.
_GET_LIST_OPENAPI = {
    "summary": "List inbound SES events across all recipients",
    "description": (
        "Newest-first list of ``SesEvent`` rows across every "
        "recipient, including events whose ``user`` FK is ``None`` "
        "(addresses with no ``User`` row). Lets an operator "
        "reconcile a campaign's bounces by window, campaign "
        "(``email_log``), recipient substring, or event type. "
        "``raw_payload`` is deliberately excluded. ``count`` is the "
        "total number of matching rows across all pages, distinct "
        "from the page length. Token-gated (staff tokens only)."
    ),
    "query": {
        "type": {
            "type": "string",
            "required": False,
            "description": (
                "Exact ``event_type`` match, or the convenience "
                "value ``bounce`` (any of bounce_permanent / "
                "bounce_transient / bounce_other)."
            ),
        },
        "since": {
            "type": "string",
            "required": False,
            "description": (
                "ISO-8601 datetime; inclusive lower bound on "
                "``received_at``."
            ),
        },
        "until": {
            "type": "string",
            "required": False,
            "description": (
                "ISO-8601 datetime; inclusive upper bound on "
                "``received_at``."
            ),
        },
        "email_log": {
            "type": "integer",
            "required": False,
            "description": (
                "Exact ``email_log_id`` filter (correlate one "
                "campaign's send)."
            ),
        },
        "recipient": {
            "type": "string",
            "required": False,
            "description": (
                "Case-insensitive substring match on ``recipient_email``."
            ),
        },
        "limit": {
            "type": "integer",
            "required": False,
            "description": "Page size; default 50, clamped to 200.",
        },
        "offset": {
            "type": "integer",
            "required": False,
            "description": "Pagination offset; default 0.",
        },
    },
    "responses": {
        200: {
            "description": "SES events page.",
            "example": {
                "ses_events": [_SES_EVENT_EXAMPLE],
                "count": 1,
                "limit": 50,
                "offset": 0,
            },
        },
        422: {
            "description": "Invalid filter values.",
            "example": {
                "error": "Invalid event type: 'invalid'",
                "code": "validation_error",
                "details": {
                    "field": "type",
                    "value": "invalid",
                    "allowed": list(VALID_SES_EVENT_TYPES),
                },
            },
        },
    },
}


@token_required
@csrf_exempt
@openapi_spec(
    tag="SES Webhook",
    summary="List SES events across all recipients",
    methods={"GET": _GET_LIST_OPENAPI},
)
def ses_events_list(request):
    """``GET /api/ses-events`` -- aggregate list across all recipients."""
    limit, err = _parse_limit(request.GET.get("limit"))
    if err is not None:
        return err
    offset, err = _parse_offset(request.GET.get("offset"))
    if err is not None:
        return err
    since, err = _parse_since(request.GET.get("since"))
    if err is not None:
        return err
    until, err = _parse_since(request.GET.get("until"), field="until")
    if err is not None:
        return err
    email_log_id, err = _parse_email_log(request.GET.get("email_log"))
    if err is not None:
        return err

    type_filter = request.GET.get("type") or ""
    if (
        type_filter
        and type_filter != _BOUNCE_ALIAS
        and type_filter not in VALID_SES_EVENT_TYPES
    ):
        return error_response(
            f"Invalid event type: {type_filter!r}",
            "validation_error",
            status=422,
            details={
                "field": "type",
                "value": type_filter,
                "allowed": list(VALID_SES_EVENT_TYPES),
            },
        )

    recipient = request.GET.get("recipient") or ""

    # Base queryset is unfiltered on ``user`` -- the whole point is to
    # include ``user=None`` rows.
    qs = SesEvent.objects.all()
    if type_filter == _BOUNCE_ALIAS:
        qs = qs.filter(event_type__in=_BOUNCE_EVENT_TYPES)
    elif type_filter:
        qs = qs.filter(event_type=type_filter)
    if since is not None:
        qs = qs.filter(received_at__gte=since)
    if until is not None:
        qs = qs.filter(received_at__lte=until)
    if email_log_id is not None:
        qs = qs.filter(email_log_id=email_log_id)
    if recipient:
        qs = qs.filter(recipient_email__icontains=recipient)

    # ``count`` is the total match set BEFORE slicing -- the reporter
    # wants the full count, not the page length.
    total = qs.count()
    qs = qs.order_by("-received_at")[offset:offset + limit]

    rows = [serialize_ses_event(e) for e in qs]
    return JsonResponse(
        {
            "ses_events": rows,
            "count": total,
            "limit": limit,
            "offset": offset,
        },
        status=200,
    )


# The webhook's existing POST operation spec is the source of truth for
# the SNS-delivered side. Compose it with the GET list operation so the
# route-bound dispatcher exposes BOTH a documented GET and the unchanged
# POST. The builder reads the spec off the dispatcher (the routed view),
# so this keeps the generated path-item rich rather than minimal.
_WEBHOOK_SPEC = getattr(_ses_events_webhook, OPENAPI_SPEC_ATTR, {}) or {}
# Copy the webhook's POST operation and pin ``security=[]`` on it (the
# webhook previously carried ``security=[]`` at the view level; here GET
# is token-gated, so security must live per-operation, not per-path).
_WEBHOOK_POST_OPENAPI = {
    **((_WEBHOOK_SPEC.get("methods") or {}).get("POST", {})),
    "security": [],
}


@csrf_exempt
@openapi_spec(
    tag="SES Webhook",
    summary="SES events: list (GET) and SNS webhook (POST)",
    description=(
        "Method dispatcher on the canonical ``/api/ses-events`` path. "
        "``GET`` is served by the token-gated aggregate list view; every "
        "other method falls through to the SNS-signature-gated webhook. "
        "Each side keeps its own auth (token vs. SNS signature)."
    ),
    methods={
        "GET": _GET_LIST_OPENAPI,
        "POST": _WEBHOOK_POST_OPENAPI,
    },
)
def ses_events_dispatch(request, *args, **kwargs):
    """Route ``GET`` to the aggregate list; everything else to the webhook.

    The two delegated views keep their own auth: ``ses_events_list`` is
    ``@token_required`` (staff tokens), the webhook is SNS-signature /
    shared-secret gated and still enforces ``@require_http_methods(["POST"])``.
    """
    if request.method == "GET":
        return ses_events_list(request, *args, **kwargs)
    return _ses_events_webhook(request, *args, **kwargs)
