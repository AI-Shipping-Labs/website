"""Cohesive route-family owner extracted from the Pods staff API."""

# The shared module exports the single wire/query contract used by each route
# family, including private serializer names.
from api.views.pod_api_shared import (
    _ERR_UNKNOWN_POD,
    TAG,
    JsonResponse,
    PodMessage,
    _get_pod,
    _parse_limit,
    _unknown_pod,
    chat_svc,
    csrf_exempt,
    display_name,
    error_response,
    isoformat_or_none,
    openapi_spec,
    require_methods,
    token_required,
)

# --- Pod chat (issue #1920) ------------------------------------------------------

MESSAGE_LIST_DEFAULT_LIMIT = 50
MESSAGE_LIST_MAX_LIMIT = 200

_MESSAGE_EXAMPLE = {
    "id": 41,
    "author_email": "raj@example.com",
    "author_name": "Raj P.",
    "body": "Hi, I am building a RAG eval harness, Berlin time",
    "created_at": "2026-10-05T18:30:00+00:00",
    "hidden": False,
    "hidden_at": None,
    "hidden_by_email": None,
}
_ERR_UNKNOWN_MESSAGE = {"error": "Message not found", "code": "unknown_message"}


def _serialize_message(message):
    hidden_by_email = None
    if message.hidden_by_id:
        hidden_by_email = message.hidden_by.email
    return {
        "id": message.pk,
        "author_email": message.author.email,
        "author_name": display_name(message.author),
        "body": message.body,
        "created_at": isoformat_or_none(message.created_at),
        "hidden": message.hidden_at is not None,
        "hidden_at": isoformat_or_none(message.hidden_at),
        "hidden_by_email": hidden_by_email,
    }


@token_required(structured_errors=True)
@csrf_exempt
@require_methods("GET", structured_errors=True)
@openapi_spec(
    tag=TAG,
    summary="List pod chat messages (staff-only)",
    methods={
        "GET": {
            "summary": "List pod chat messages (staff-only)",
            "description": (
                "Every chat message of the pod, oldest first, including "
                "messages hidden by staff (``hidden: true``). Plain text as "
                "posted. Page with ``limit`` and ``offset``; ``total`` counts "
                "every message. There is no create or delete endpoint: hide is "
                "the moderation tool."
            ),
            "query": {
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MESSAGE_LIST_MAX_LIMIT,
                    "default": MESSAGE_LIST_DEFAULT_LIMIT,
                },
                "offset": {"type": "integer", "minimum": 0, "default": 0},
            },
            "responses": {
                200: {
                    "description": "Message page.",
                    "example": {
                        "total": 1,
                        "limit": MESSAGE_LIST_DEFAULT_LIMIT,
                        "offset": 0,
                        "messages": [_MESSAGE_EXAMPLE],
                    },
                },
                401: {"description": "Missing or invalid staff token."},
                404: {"description": "Unknown pod.", "example": _ERR_UNKNOWN_POD},
                422: {
                    "description": "Bad limit or offset.",
                    "example": {
                        "error": "limit must be between 1 and 200; offset must be 0 or more",
                        "code": "validation_error",
                    },
                },
            },
        },
    },
)
def pod_messages(request, pod_id):
    pod = _get_pod(pod_id)
    if pod is None:
        return _unknown_pod()
    limit, offset, error = _parse_limit(request, MESSAGE_LIST_DEFAULT_LIMIT, MESSAGE_LIST_MAX_LIMIT)
    if error is not None:
        return error
    messages = PodMessage.objects.filter(pod=pod).select_related("author", "hidden_by").order_by("created_at", "pk")
    return JsonResponse(
        {
            "total": messages.count(),
            "limit": limit,
            "offset": offset,
            "messages": [_serialize_message(m) for m in messages[offset : offset + limit]],
        }
    )


def _moderation_spec(verb, description):
    hidden_at = None
    hidden_by_email = None
    if verb == "Hide":
        hidden_at = "2026-10-05T19:00:00+00:00"
        hidden_by_email = "staff@example.com"
    return {
        "POST": {
            "summary": f"{verb} a pod chat message (staff-only)",
            "description": description,
            "request_body": {"body_required": False, "properties": {}},
            "responses": {
                200: {
                    "description": "The message.",
                    "example": {
                        **_MESSAGE_EXAMPLE,
                        "hidden": verb == "Hide",
                        "hidden_at": hidden_at,
                        "hidden_by_email": hidden_by_email,
                    },
                },
                401: {"description": "Missing or invalid staff token."},
                404: {"description": "Unknown pod, or a message of another pod.", "example": _ERR_UNKNOWN_MESSAGE},
            },
        },
    }


def _moderate(pod_id, message_id, action):
    pod = _get_pod(pod_id)
    if pod is None:
        return _unknown_pod()
    message = PodMessage.objects.filter(pk=message_id, pod=pod).first()
    if message is None:
        return error_response("Message not found", "unknown_message", status=404)
    action(message)
    message = PodMessage.objects.select_related("author", "hidden_by").get(pk=message.pk)
    return JsonResponse(_serialize_message(message))


@token_required(structured_errors=True)
@csrf_exempt
@require_methods("POST", structured_errors=True)
@openapi_spec(
    tag=TAG,
    summary="Hide a pod chat message (staff-only)",
    methods=_moderation_spec(
        "Hide",
        "Hides the message from pod members; it stays in Studio and this API. "
        "Sets ``hidden_at`` and ``hidden_by`` (the token owner). Idempotent: "
        "hiding a hidden message keeps the first hide.",
    ),
)
def pod_message_hide(request, pod_id, message_id):
    return _moderate(pod_id, message_id, lambda message: chat_svc.hide_message(message, request.user))


@token_required(structured_errors=True)
@csrf_exempt
@require_methods("POST", structured_errors=True)
@openapi_spec(
    tag=TAG,
    summary="Restore a pod chat message (staff-only)",
    methods=_moderation_spec(
        "Restore",
        "Shows a hidden message to pod members again and clears ``hidden_at`` and ``hidden_by``. Idempotent.",
    ),
)
def pod_message_restore(request, pod_id, message_id):
    return _moderate(pod_id, message_id, chat_svc.restore_message)
