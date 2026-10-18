"""Cohesive route-family owner extracted from the Pods staff API."""

# The shared module exports the single wire/query contract used by each route
# family, including private serializer names.
from api.views.pod_api_shared import (
    _ERR_UNKNOWN_POD,
    _POD_DETAIL_EXAMPLE,
    _POD_WRITABLE,
    _STATUS_VALUES,
    TAG,
    JsonResponse,
    _get_pod,
    _pod_error_response,
    _serialize_detail,
    _unknown_pod,
    body_must_be_object_response,
    clean_channel_url,
    csrf_exempt,
    mtg,
    normalize_email,
    openapi_spec,
    parse_json_body,
    require_methods,
    resolve_users_by_emails,
    svc,
    token_required,
    validation_response,
)

# --- Pod detail -------------------------------------------------------------


def _owner_change(pod, raw_email):
    email = normalize_email(raw_email or "")
    if not email:
        return None, None
    owner = resolve_users_by_emails([email]).get(email)
    if owner is None or not pod.memberships.filter(user=owner).exists():
        error = validation_response(
            {"field": "owner_email"},
            message="The owner must be a member of the pod.",
        )
        return None, error
    return owner, None


def _copy_link_changes(data, changes):
    if "slack_channel_url" in data:
        try:
            changes["slack_channel_url"] = clean_channel_url(data["slack_channel_url"] or "")
        except ValueError as exc:
            return validation_response({"field": "slack_channel_url"}, message=str(exc))
    if "meeting_url" not in data:
        return None
    if data["meeting_url"] is not None and not isinstance(data["meeting_url"], str):
        return validation_response({"field": "meeting_url", "expected": "string"}, message=mtg.MSG_CALL_LINK)
    try:
        changes["meeting_url"] = mtg.clean_call_link(data["meeting_url"] or "")
    except ValueError as exc:
        return validation_response({"field": "meeting_url"}, message=str(exc))
    return None


def _base_patch_changes(data):
    unknown = sorted(set(data) - set(_POD_WRITABLE))
    if unknown:
        error = validation_response(
            {"field": unknown[0], "allowed": list(_POD_WRITABLE)},
            message=f"Unknown field(s): {', '.join(unknown)}",
        )
        return None, error
    if not data:
        error = validation_response(
            {"field": "body", "allowed": list(_POD_WRITABLE)},
            message="Nothing to update",
        )
        return None, error
    if "max_members" in data:
        error = validation_response(
            {"field": "max_members"},
            message=svc.MSG_MAX_MEMBERS_RETIRED,
        )
        return None, error
    changes = {}
    link_fields = ("owner_email", "slack_channel_url", "meeting_url")
    for key, value in data.items():
        if key not in link_fields:
            changes[key] = value
    return changes, None


def _patch_pod(request, pod):
    data, parse_error = parse_json_body(request)
    if parse_error is not None:
        return parse_error
    if not isinstance(data, dict):
        return body_must_be_object_response()
    changes, input_error = _base_patch_changes(data)
    if input_error is not None:
        return input_error
    if "owner_email" in data:
        owner, owner_error = _owner_change(pod, data["owner_email"])
        if owner_error is not None:
            return owner_error
        changes["owner"] = owner
    link_error = _copy_link_changes(data, changes)
    if link_error is not None:
        return link_error
    try:
        svc.update_pod(pod, changes, actor=request.user, staff=True)
    except svc.PodError as exc:
        return _pod_error_response(exc)
    return JsonResponse(_serialize_detail(pod))


@token_required(structured_errors=True)
@csrf_exempt
@require_methods("GET", "PATCH", structured_errors=True)
@openapi_spec(
    tag=TAG,
    summary="Read or update a pod (staff-only)",
    methods={
        "GET": {
            "summary": "Pod detail (staff-only)",
            "description": (
                "Settings, members, public scheduling guidance, staff-only "
                "internal formation notes, the Slack channel link, "
                "the call link ``meeting_url``, ``message_count`` and "
                "``last_message_at`` (visible pod chat messages only; ``null`` "
                "without any), ``artifact_count`` (meeting notes and "
                "transcripts), ``meetings`` (every meeting that "
                "is not cancelled, same shape as ``GET /api/pods/<id>/meetings``) "
                "and ``suggested_slots``: up to ``PODS_SUGGESTION_COUNT`` UTC "
                "slots with ``available``, ``if_needed`` and ``missing`` member "
                "emails and each member's local start time."
            ),
            "responses": {
                200: {"description": "Pod detail.", "example": _POD_DETAIL_EXAMPLE},
                401: {"description": "Missing or invalid staff token."},
                404: {"description": "Unknown pod.", "example": _ERR_UNKNOWN_POD},
            },
        },
        "PATCH": {
            "summary": "Update a pod (staff-only)",
            "description": (
                "Partial update. ``max_members`` is retired and returns 422. "
                "``owner_email`` must be a member, "
                "or ``null`` for no owner. ``status=archived`` hides the pod from "
                "members; there is no DELETE. Historical requests are retained. "
                "``slack_channel_url`` must be an https Slack link (empty clears). "
                "``meeting_url`` is the pod call link: https only, up to 500 "
                "characters, empty clears. ``meeting_count`` below the meetings "
                "already planned or held returns 422."
            ),
            "request_body": {
                "properties": {
                    "name": {"type": "string"},
                    "purpose": {"type": "string", "maxLength": 160},
                    "scheduling_note": {"type": "string", "maxLength": 500},
                    "internal_notes": {"type": "string", "maxLength": 2000},
                    "max_members": {"description": "Retired; always returns 422."},
                    "meeting_count": {"type": "integer", "minimum": 1, "maximum": 20},
                    "meeting_minutes": {"type": "integer", "enum": [30, 45, 60, 90]},
                    "status": {"type": "string", "enum": _STATUS_VALUES},
                    "owner_email": {"type": ["string", "null"]},
                    "slack_channel_url": {"type": "string"},
                    "meeting_url": {"type": ["string", "null"], "maxLength": 500},
                },
                "example": {"scheduling_note": "Meet on Tuesdays when possible.", "status": "closed"},
            },
            "responses": {
                200: {"description": "Updated pod.", "example": _POD_DETAIL_EXAMPLE},
                400: {"description": "Invalid JSON or non-object body."},
                401: {"description": "Missing or invalid staff token."},
                404: {"description": "Unknown pod.", "example": _ERR_UNKNOWN_POD},
                422: {
                    "description": "Validation error.",
                    "example": {"error": "Study groups no longer have a size limit.", "code": "validation_error"},
                },
            },
        },
    },
)
def pod_detail(request, pod_id):
    pod = _get_pod(pod_id)
    if pod is None:
        return _unknown_pod()
    if request.method == "PATCH":
        return _patch_pod(request, pod)
    return JsonResponse(_serialize_detail(pod))
