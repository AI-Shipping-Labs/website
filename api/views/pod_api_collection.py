"""Cohesive route-family owner extracted from the Pods staff API."""

# The shared module exports the single wire/query contract used by each route
# family, including private serializer names.
from api.views.pod_api_collection_spec import COLLECTION_METHODS
from api.views.pod_api_shared import (
    _STATUS_VALUES,
    POD_LIST_DEFAULT_LIMIT,
    POD_LIST_MAX_LIMIT,
    POD_SOURCE_API,
    TAG,
    Course,
    JsonResponse,
    Sprint,
    _parse_limit,
    _pod_error_response,
    _pod_queryset,
    _serialize_detail,
    _serialize_summary,
    _unknown_sprint,
    body_must_be_object_response,
    cohort_activity,
    csrf_exempt,
    error_response,
    get_course_cohort_by_key,
    openapi_spec,
    parse_json_body,
    require_methods,
    sprint_activity,
    svc,
    token_required,
    validation_response,
)

# --- Pods collection --------------------------------------------------------


def _create_activity(data, sprint_slug):
    if sprint_slug:
        sprint = Sprint.objects.filter(slug=sprint_slug).first()
        if sprint is None:
            return None, _unknown_sprint()
        return sprint_activity(sprint), None
    course = Course.objects.filter(slug=str(data["course_slug"]).strip()).first()
    if course is None:
        return None, error_response("Course not found", "unknown_course", status=404)
    cohort = get_course_cohort_by_key(course, data["cohort_key"])
    if cohort is None:
        error = error_response(
            f"Unknown cohort {data['cohort_key']!r} for course {course.slug!r}",
            "unknown_cohort",
            status=404,
        )
        return None, error
    return cohort_activity(cohort), None


def _pod_create_fields(data):
    names = (
        "name",
        "purpose",
        "scheduling_note",
        "internal_notes",
        "max_members",
        "meeting_count",
        "meeting_minutes",
    )
    fields = {}
    for key in names:
        if key in data:
            fields[key] = data.get(key)
    return fields


def _create_input(data):
    sprint_slug = str(data.get("sprint_slug") or "").strip()
    has_course = bool(str(data.get("course_slug") or "").strip() or str(data.get("cohort_key") or "").strip())
    if sprint_slug and has_course:
        error = validation_response(
            {"field": "sprint_slug"},
            message="Give sprint_slug or course_slug and cohort_key, not both",
        )
        return None, None, error
    required = ("course_slug", "cohort_key", "name", "purpose")
    if sprint_slug:
        required = ("name", "purpose")
    for field in required:
        if not str(data.get(field) or "").strip():
            error = validation_response({"field": field}, message=f"{field} is required")
            return None, None, error
    member_emails = data.get("member_emails") or []
    if not isinstance(member_emails, list):
        error = validation_response(
            {"field": "member_emails", "expected": "array of strings"},
            message="member_emails must be a list",
        )
        return None, None, error
    return sprint_slug, member_emails, None


def _create_pod(request):
    data, parse_error = parse_json_body(request)
    if parse_error is not None:
        return parse_error
    if not isinstance(data, dict):
        return body_must_be_object_response()
    sprint_slug, member_emails, input_error = _create_input(data)
    if input_error is not None:
        return input_error
    activity, activity_error = _create_activity(data, sprint_slug)
    if activity_error is not None:
        return activity_error
    try:
        pod, results, created = svc.create_staff_pod(
            activity=activity,
            data=_pod_create_fields(data),
            owner_email=data.get("owner_email") or "",
            member_emails=member_emails,
            actor=request.user,
            source=POD_SOURCE_API,
        )
    except svc.PodError as exc:
        return _pod_error_response(exc)
    payload = _serialize_detail(pod)
    payload["results"] = results.as_dict()
    status = 200
    if created:
        status = 201
    return JsonResponse(payload, status=status)


def _activity_filter(pods, course_slug, cohort_key, sprint_slug):
    if sprint_slug:
        if course_slug or cohort_key:
            error = validation_response({"field": "sprint"}, message="sprint cannot be combined with course or cohort")
            return None, error
        sprint = Sprint.objects.filter(slug=sprint_slug).first()
        if sprint is None:
            return None, _unknown_sprint()
        return pods.filter(sprint=sprint), None
    if course_slug:
        course = Course.objects.filter(slug=course_slug).first()
        if course is None:
            return None, error_response("Course not found", "unknown_course", status=404)
        pods = pods.filter(cohort__course=course)
        if cohort_key:
            cohort = get_course_cohort_by_key(course, cohort_key)
            if cohort is None:
                error = error_response(
                    f"Unknown cohort {cohort_key!r} for course {course.slug!r}",
                    "unknown_cohort",
                    status=404,
                )
                return None, error
            pods = pods.filter(cohort=cohort)
        return pods, None
    if cohort_key:
        return None, validation_response({"field": "cohort"}, message="cohort needs course")
    return pods, None


def _collection_queryset(request):
    pods = _pod_queryset().order_by("-created_at", "-pk")
    course_slug = (request.GET.get("course") or "").strip()
    cohort_key = (request.GET.get("cohort") or "").strip()
    sprint_slug = (request.GET.get("sprint") or "").strip()
    pods, error = _activity_filter(pods, course_slug, cohort_key, sprint_slug)
    if error is not None:
        return None, error
    status = (request.GET.get("status") or "").strip()
    if status:
        if status not in _STATUS_VALUES:
            error = validation_response({"field": "status", "allowed": _STATUS_VALUES}, message="Unknown status")
            return None, error
        pods = pods.filter(status=status)
    return pods, None


@token_required(structured_errors=True)
@csrf_exempt
@require_methods("GET", "POST", structured_errors=True)
@openapi_spec(
    tag=TAG,
    summary="List or create pods (staff-only)",
    methods=COLLECTION_METHODS,
)
def pods_collection(request):
    if request.method == "POST":
        return _create_pod(request)
    pods, filter_error = _collection_queryset(request)
    if filter_error is not None:
        return filter_error
    limit, offset, error = _parse_limit(request, POD_LIST_DEFAULT_LIMIT, POD_LIST_MAX_LIMIT)
    if error is not None:
        return error
    total = pods.count()
    return JsonResponse(
        {
            "total": total,
            "limit": limit,
            "offset": offset,
            "pods": [_serialize_summary(pod) for pod in pods[offset : offset + limit]],
        }
    )
