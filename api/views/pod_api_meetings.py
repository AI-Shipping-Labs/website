"""Cohesive route-family owner extracted from the Pods staff API."""

# The shared module exports the single wire/query contract used by each route
# family, including private serializer names.
from api.views.pod_api_shared import (
    _ERR_LIMIT,
    _ERR_UNKNOWN_POD,
    _MEETING_CREATE_FIELDS,
    _MEETING_EXAMPLE,
    _MEETING_PATCH_FIELDS,
    _MEETING_PATCH_STATUSES,
    _MEETING_STATUS_VALUES,
    POD_SOURCE_API,
    TAG,
    JsonResponse,
    PodMeeting,
    _get_pod,
    _pod_error_response,
    _serialize_meetings,
    _unknown_pod,
    body_must_be_object_response,
    csrf_exempt,
    error_response,
    mtg,
    openapi_spec,
    parse_json_body,
    require_methods,
    svc,
    token_required,
    validation_response,
)

# --- Meetings (issue #1919) -----------------------------------------------------


def _parse_bool(data, field, default=False):
    value = data.get(field, default)
    if not isinstance(value, bool):
        return None, validation_response(
            {"field": field, "expected": "boolean"}, message=f"{field} must be true or false"
        )
    return value, None


def _meeting_create_input(request):
    data, parse_error = parse_json_body(request)
    if parse_error is not None:
        return None, None, None, parse_error
    if not isinstance(data, dict):
        return None, None, None, body_must_be_object_response()
    unknown = sorted(set(data) - set(_MEETING_CREATE_FIELDS))
    if unknown:
        error = validation_response(
            {"field": unknown[0], "allowed": list(_MEETING_CREATE_FIELDS)},
            message=f"Unknown field(s): {', '.join(unknown)}",
        )
        return None, None, None, error
    for field in ("starts_at", "timezone"):
        if not isinstance(data.get(field), str) or not data[field].strip():
            error = validation_response({"field": field}, message=f"{field} is required")
            return None, None, None, error
    repeat_weekly, error = _parse_bool(data, "repeat_weekly")
    if error is not None:
        return None, None, None, error
    count = data.get("count")
    if count is not None and (isinstance(count, bool) or not isinstance(count, int) or count < 1):
        error = validation_response({"field": "count", "expected": "integer >= 1"}, message="count must be 1 or more")
        return None, None, None, error
    return data, repeat_weekly, count, None


def _create_meetings(request, pod):
    data, repeat_weekly, count, error = _meeting_create_input(request)
    if error is not None:
        return error
    try:
        start = mtg.parse_instant(data["starts_at"], field="starts_at")
        created = mtg.schedule_meetings(
            pod,
            request.user,
            start,
            zone_name=data["timezone"],
            repeat_weekly=repeat_weekly,
            count=count,
            created_via=POD_SOURCE_API,
        )
    except svc.PodError as exc:
        return _pod_error_response(exc)
    return JsonResponse({"meetings": _serialize_meetings(pod, only_ids={m.pk for m in created})}, status=201)


@token_required(structured_errors=True)
@csrf_exempt
@require_methods("GET", "POST", structured_errors=True)
@openapi_spec(
    tag=TAG,
    summary="List or schedule pod meetings (staff-only)",
    methods={
        "GET": {
            "summary": "List pod meetings (staff-only)",
            "description": (
                "Every meeting of the pod in start order, including cancelled "
                "ones; filter with ``status``. ``number`` is the 1-based position "
                "among meetings that are neither cancelled nor expired (``null`` "
                "otherwise). A proposal is ``expired`` once its start passed "
                "while it was still ``proposed``. ``responses`` hold the explicit "
                "answers; after agreement a member without an answer counts as "
                "going. On a live proposal head (the first meeting of a weekly "
                "proposal) ``waiting_on`` lists the member emails with no answer "
                "yet, ``stuck`` is true once it waited more than "
                "``PODS_STUCK_PROPOSAL_HOURS`` or half the time from when it was "
                "proposed or last moved to its start, whichever is shorter, on a "
                "non-archived pod, and ``confirmable`` is false once too many "
                "current members answered ``cant_make_it`` for it to reach the "
                "required attendance (Studio shows ``Can't be confirmed``); "
                "every other meeting has ``waiting_on: []``, ``stuck: false`` "
                "and ``confirmable: null``. ``stuck_alerted_at`` is when the "
                "daily staff Slack alert announced it (cleared on move)."
            ),
            "query": {"status": {"type": "string", "enum": _MEETING_STATUS_VALUES}},
            "responses": {
                200: {"description": "Meetings.", "example": {"meetings": [_MEETING_EXAMPLE]}},
                401: {"description": "Missing or invalid staff token."},
                404: {"description": "Unknown pod.", "example": _ERR_UNKNOWN_POD},
                422: {"description": "Unknown status filter."},
            },
        },
        "POST": {
            "summary": "Schedule pod meetings (staff-only)",
            "description": (
                "Creates confirmed (``scheduled``) meetings with "
                "``created_via=api`` and notifies the members. ``starts_at`` is "
                "an ISO 8601 instant with an offset, on the quarter hour, at "
                "least 1 hour ahead and at most 180 days ahead. ``timezone`` is "
                "the IANA zone weekly repeats keep their local time in. "
                "``count`` defaults to 1, or to every remaining meeting with "
                "``repeat_weekly``; more than 1 needs ``repeat_weekly``. A repeat "
                "that overlaps another meeting moves on a week. Over "
                "``meeting_count`` returns 409 ``meeting_limit_reached``."
            ),
            "request_body": {
                "required": ["starts_at", "timezone"],
                "properties": {
                    "starts_at": {"type": "string", "format": "date-time"},
                    "timezone": {"type": "string"},
                    "repeat_weekly": {"type": "boolean", "default": False},
                    "count": {"type": "integer", "minimum": 1},
                },
                "example": {
                    "starts_at": "2026-10-13T18:00:00+02:00",
                    "timezone": "Europe/Berlin",
                    "repeat_weekly": True,
                },
            },
            "responses": {
                201: {"description": "Created meetings.", "example": {"meetings": [_MEETING_EXAMPLE]}},
                400: {"description": "Invalid JSON or non-object body."},
                401: {"description": "Missing or invalid staff token."},
                404: {"description": "Unknown pod.", "example": _ERR_UNKNOWN_POD},
                409: {"description": "The pod already has all its meetings planned.", "example": _ERR_LIMIT},
                422: {
                    "description": "Bad time (past, over 180 days, off the quarter hour), overlap, or bad field.",
                    "example": {"error": mtg.MSG_TIME_RANGE, "code": "validation_error", "details": {"field": "start"}},
                },
            },
        },
    },
)
def pod_meetings(request, pod_id):
    pod = _get_pod(pod_id)
    if pod is None:
        return _unknown_pod()
    if request.method == "POST":
        return _create_meetings(request, pod)
    status = (request.GET.get("status") or "").strip()
    if status and status not in _MEETING_STATUS_VALUES:
        return validation_response({"field": "status", "allowed": _MEETING_STATUS_VALUES}, message="Unknown status")
    return JsonResponse({"meetings": _serialize_meetings(pod, status=status or None)})


def _meeting_patch_input(request, pod_id, meeting_id):
    pod = _get_pod(pod_id)
    if pod is None:
        return None, None, _unknown_pod()
    meeting = PodMeeting.objects.filter(pk=meeting_id, pod=pod).first()
    if meeting is None:
        return None, None, error_response("Meeting not found", "unknown_meeting", status=404)
    data, parse_error = parse_json_body(request)
    if parse_error is not None:
        return None, None, parse_error
    if not isinstance(data, dict):
        return None, None, body_must_be_object_response()
    unknown = sorted(set(data) - set(_MEETING_PATCH_FIELDS))
    if unknown:
        error = validation_response(
            {"field": unknown[0], "allowed": list(_MEETING_PATCH_FIELDS)},
            message=f"Unknown field(s): {', '.join(unknown)}",
        )
        return None, None, error
    if ("starts_at" in data) == ("status" in data):
        error = validation_response(
            {"field": "body", "allowed": list(_MEETING_PATCH_FIELDS)},
            message="Send either starts_at or status",
        )
        return None, None, error
    return pod, meeting, data


def _patch_meeting(data, meeting, actor):
    if "status" in data:
        if data["status"] not in _MEETING_PATCH_STATUSES:
            return None, validation_response(
                {"field": "status", "allowed": _MEETING_PATCH_STATUSES},
                message="Unknown status",
            )
        return mtg.set_meeting_status(meeting, actor, data["status"]), None
    move_later, error = _parse_bool(data, "move_later")
    if error is not None:
        return None, error
    zone = data.get("timezone") or meeting.timezone
    if not isinstance(zone, str):
        return None, validation_response(
            {"field": "timezone", "expected": "string"},
            message=mtg.MSG_BAD_ZONE,
        )
    start = mtg.parse_instant(data["starts_at"], field="starts_at")
    return mtg.move_meeting(meeting, actor, start, zone_name=zone, move_later=move_later), None


@token_required(structured_errors=True)
@csrf_exempt
@require_methods("PATCH", structured_errors=True)
@openapi_spec(
    tag=TAG,
    summary="Move a pod meeting or set its status (staff-only)",
    methods={
        "PATCH": {
            "summary": "Move a meeting or set its status (staff-only)",
            "description": (
                "Send ``starts_at`` to move (with optional ``timezone``, default "
                "the meeting's own zone, and ``move_later`` to move the later "
                "meetings of its weekly series to the same weekly time; all or "
                "nothing on overlap), or ``status`` (``scheduled``, ``held`` or "
                "``cancelled``) to set it as a staff override. Moving keeps the "
                "status, clears ``Can't make it`` answers and the reminder. "
                "Moving a past, held or cancelled meeting returns 409 "
                "``meeting_not_movable``; ``held`` on a proposal returns 422 "
                "(schedule it first); ``cancelled`` on a proposal cancels the "
                "whole proposal; bringing a meeting back to "
                "``scheduled`` over ``meeting_count`` returns 409 "
                "``meeting_limit_reached``. Returns the changed meetings. There "
                "is no DELETE: cancel instead so history is kept."
            ),
            "request_body": {
                "properties": {
                    "starts_at": {"type": "string", "format": "date-time"},
                    "timezone": {"type": "string"},
                    "move_later": {"type": "boolean", "default": False},
                    "status": {"type": "string", "enum": _MEETING_PATCH_STATUSES},
                },
                "example": {"starts_at": "2026-10-15T17:00:00+02:00", "timezone": "Europe/Berlin", "move_later": True},
            },
            "responses": {
                200: {"description": "Changed meetings.", "example": {"meetings": [_MEETING_EXAMPLE]}},
                400: {"description": "Invalid JSON or non-object body."},
                401: {"description": "Missing or invalid staff token."},
                404: {
                    "description": "Unknown pod or a meeting of another pod.",
                    "example": {"error": "Meeting not found", "code": "unknown_meeting"},
                },
                409: {
                    "description": "Meeting cannot be moved, or the meeting limit is reached.",
                    "example": {"error": mtg.MSG_HAPPENED, "code": mtg.CODE_NOT_MOVABLE},
                },
                422: {
                    "description": "Validation error, including status held before the meeting starts.",
                    "example": {"error": mtg.MSG_QUARTER, "code": "validation_error", "details": {"field": "start"}},
                },
            },
        },
    },
)
def pod_meeting_detail(request, pod_id, meeting_id):
    pod, meeting, result = _meeting_patch_input(request, pod_id, meeting_id)
    if not isinstance(result, dict):
        return result
    try:
        changed, error = _patch_meeting(result, meeting, request.user)
    except svc.PodError as exc:
        return _pod_error_response(exc)
    if error is not None:
        return error
    return JsonResponse({"meetings": _serialize_meetings(pod, only_ids={m.pk for m in changed})})
