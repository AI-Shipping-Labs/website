"""Cohesive route-family owner extracted from the Pods staff API."""

# The shared module exports the single wire/query contract used by each route
# family, including private serializer names.
from api.views.pod_api_shared import (
    _ERR_UNKNOWN_POD,
    _POD_DETAIL_EXAMPLE,
    _REQUEST_EXAMPLE,
    _REQUEST_STATUS_VALUES,
    _RESULTS_EXAMPLE,
    POD_SOURCE_API,
    TAG,
    HttpResponse,
    JsonResponse,
    _get_pod,
    _pod_error_response,
    _serialize_detail,
    _serialize_request,
    _unknown_pod,
    body_must_be_object_response,
    csrf_exempt,
    error_response,
    normalize_email,
    openapi_spec,
    parse_json_body,
    require_methods,
    resolve_users_by_emails,
    svc,
    token_required,
    validation_response,
)


@token_required(structured_errors=True)
@csrf_exempt
@require_methods("POST", structured_errors=True)
@openapi_spec(
    tag=TAG,
    summary="Add pod members (staff-only)",
    methods={
        "POST": {
            "summary": "Add members by email (staff-only)",
            "description": (
                "Idempotent. Adds enrolled cohort (or sprint) members without a size limit. "
                "Returns the same ``results`` buckets as pod create."
            ),
            "request_body": {
                "required": ["emails"],
                "properties": {"emails": {"type": "array", "items": {"type": "string"}}},
                "example": {"emails": ["raj@example.com"]},
            },
            "responses": {
                200: {
                    "description": "Per-email results and the pod.",
                    "example": {"results": _RESULTS_EXAMPLE, "pod": _POD_DETAIL_EXAMPLE},
                },
                400: {"description": "Invalid JSON or non-object body."},
                401: {"description": "Missing or invalid staff token."},
                404: {"description": "Unknown pod.", "example": _ERR_UNKNOWN_POD},
                422: {"description": "emails missing or not a list."},
            },
        },
    },
)
def pod_members(request, pod_id):
    pod = _get_pod(pod_id)
    if pod is None:
        return _unknown_pod()
    data, parse_error = parse_json_body(request)
    if parse_error is not None:
        return parse_error
    if not isinstance(data, dict):
        return body_must_be_object_response()
    emails = data.get("emails")
    if not isinstance(emails, list):
        return validation_response({"field": "emails", "expected": "array of strings"}, message="emails must be a list")
    results = svc.add_members_by_email(pod, emails, actor=request.user, source=POD_SOURCE_API)
    return JsonResponse({"results": results.as_dict(), "pod": _serialize_detail(pod)})


@token_required(structured_errors=True)
@csrf_exempt
@require_methods("DELETE", structured_errors=True)
@openapi_spec(
    tag=TAG,
    summary="Remove a pod member (staff-only)",
    methods={
        "DELETE": {
            "summary": "Remove a member (staff-only)",
            "description": (
                "Removes the member with the normal leave rules: an owner hands "
                "ownership to the earliest-joined remaining member, and an "
                "emptied member-started pod is archived."
            ),
            "responses": {
                204: {"description": "Removed."},
                401: {"description": "Missing or invalid staff token."},
                404: {
                    "description": "Unknown pod or not a member.",
                    "example": {"error": svc.MSG_NOT_A_MEMBER, "code": "not_a_member"},
                },
            },
        },
    },
)
def pod_member_detail(request, pod_id, email):
    pod = _get_pod(pod_id)
    if pod is None:
        return _unknown_pod()
    normalized = normalize_email(email)
    user = None
    if normalized:
        user = resolve_users_by_emails([normalized]).get(normalized)
    if user is None or not pod.memberships.filter(user=user).exists():
        return error_response(svc.MSG_NOT_A_MEMBER, "not_a_member", status=404)
    try:
        svc.remove_member(pod, user, actor=request.user)
    except svc.PodError as exc:
        return _pod_error_response(exc)
    return HttpResponse(status=204)


@token_required(structured_errors=True)
@csrf_exempt
@require_methods("GET", structured_errors=True)
@openapi_spec(
    tag=TAG,
    summary="List pod requests (staff-only)",
    methods={
        "GET": {
            "summary": "List join requests (staff-only)",
            "description": "Historical requests for the pod, oldest first; filter with ``status``. Read-only.",
            "query": {"status": {"type": "string", "enum": _REQUEST_STATUS_VALUES}},
            "responses": {
                200: {"description": "Requests.", "example": {"requests": [_REQUEST_EXAMPLE]}},
                401: {"description": "Missing or invalid staff token."},
                404: {"description": "Unknown pod.", "example": _ERR_UNKNOWN_POD},
                422: {"description": "Unknown status filter."},
            },
        },
    },
)
def pod_requests(request, pod_id):
    pod = _get_pod(pod_id)
    if pod is None:
        return _unknown_pod()
    requests = pod.join_requests.select_related("user", "decided_by").order_by("created_at", "pk")
    status = (request.GET.get("status") or "").strip()
    if status:
        if status not in _REQUEST_STATUS_VALUES:
            return validation_response({"field": "status", "allowed": _REQUEST_STATUS_VALUES}, message="Unknown status")
        requests = requests.filter(status=status)
    return JsonResponse({"requests": [_serialize_request(r) for r in requests]})


@token_required(structured_errors=True)
@csrf_exempt
@require_methods("POST", structured_errors=True)
@openapi_spec(
    tag=TAG,
    summary="Retired study group request action",
    methods={
        "POST": {
            "description": "The approval queue is retired; stale writes never change requests or memberships.",
            "request_body": {"body_required": False, "properties": {}},
            "responses": {
                401: {"description": "Missing or invalid staff token."},
                410: {
                    "description": "Study group request actions are retired.",
                    "example": {"error": svc.MSG_REQUESTS_RETIRED, "code": "gone"},
                },
            },
        },
    },
)
def pod_request_retired(request, pod_id, request_id):
    return error_response(svc.MSG_REQUESTS_RETIRED, "gone", status=410)
