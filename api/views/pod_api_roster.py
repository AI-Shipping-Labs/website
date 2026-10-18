"""Cohesive route-family owner extracted from the Pods staff API."""

# The shared module exports the single wire/query contract used by each route
# family, including private serializer names.
from api.views.pod_api_shared import (
    _ERR_UNKNOWN_SPRINT,
    _ROSTER_EXAMPLE,
    DEFAULT_LIMIT,
    MAX_LIMIT,
    TAG,
    AvailabilityProfile,
    CohortEnrollment,
    Course,
    CRMRecord,
    JsonResponse,
    Membership,
    PodMembership,
    Sprint,
    SprintEnrollment,
    TierOverride,
    _parse_limit,
    _unknown_sprint,
    cohort_activity,
    csrf_exempt,
    error_response,
    format_minute,
    get_course_cohort_by_key,
    isoformat_or_none,
    openapi_spec,
    require_methods,
    sprint_activity,
    timezone,
    token_required,
)

# --- Roster -----------------------------------------------------------------


def _effective_tiers(user_ids):
    """``{user_id: tier_slug}``: max of paid tier and active override."""
    tiers = {}
    for membership in Membership.objects.filter(user_id__in=user_ids).select_related("tier"):
        if membership.tier_id is not None:
            tiers[membership.user_id] = (membership.tier.level, membership.tier.slug)
    for override in TierOverride.objects.filter(
        user_id__in=user_ids,
        is_active=True,
        expires_at__gt=timezone.now(),
    ).select_related("override_tier"):
        current = tiers.get(override.user_id, (0, "free"))
        if override.override_tier.level > current[0]:
            tiers[override.user_id] = (override.override_tier.level, override.override_tier.slug)
    return {user_id: slug for user_id, (_level, slug) in tiers.items()}


@token_required(structured_errors=True)
@csrf_exempt
@require_methods("GET", structured_errors=True)
@openapi_spec(
    tag=TAG,
    summary="Cohort roster for pairing (staff-only)",
    methods={
        "GET": {
            "summary": "List a cohort roster with CRM, availability and pods (staff-only)",
            "description": (
                "Every user with a ``CohortEnrollment`` in the cohort (and only "
                "them), ordered by enrollment time. ``<key>`` is the cohort "
                "external key (the ``?cohort=`` value), case-insensitive. "
                "``tier`` is the effective tier slug (paid tier or active "
                "override, whichever is higher). ``crm`` is ``null`` without a "
                "CRM record; ``availability`` is ``null`` without a profile and "
                "its windows are local wall-clock times in its ``timezone``. "
                "``pod_ids`` are the cohort study groups the user is in."
            ),
            "query": {
                "limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT, "default": DEFAULT_LIMIT},
                "offset": {"type": "integer", "minimum": 0, "default": 0},
            },
            "responses": {
                200: {
                    "description": "Roster page.",
                    "example": {
                        "course": "ai-buildcamp",
                        "cohort_key": "4",
                        "total": 1,
                        "limit": DEFAULT_LIMIT,
                        "offset": 0,
                        "members": [_ROSTER_EXAMPLE],
                    },
                },
                401: {
                    "description": "Missing or invalid staff token.",
                    "example": {"error": "Authentication token required", "code": "authentication_required"},
                },
                404: {
                    "description": "Unknown course or cohort.",
                    "example": {"error": "Unknown cohort '9' for course 'ai-buildcamp'", "code": "unknown_cohort"},
                },
                422: {
                    "description": "Bad limit or offset.",
                    "example": {
                        "error": "limit must be between 1 and 500; offset must be 0 or more",
                        "code": "validation_error",
                    },
                },
            },
        },
    },
)
def cohort_roster(request, slug, key):
    course = Course.objects.filter(slug=slug).first()
    if course is None:
        return error_response("Course not found", "unknown_course", status=404)
    cohort = get_course_cohort_by_key(course, key)
    if cohort is None:
        return error_response(f"Unknown cohort {key!r} for course {course.slug!r}", "unknown_cohort", status=404)
    limit, offset, error = _parse_limit(request, DEFAULT_LIMIT, MAX_LIMIT)
    if error is not None:
        return error
    enrollments = CohortEnrollment.objects.filter(cohort=cohort).select_related("user").order_by("enrolled_at", "pk")
    total, members = _roster_page(enrollments, cohort_activity(cohort), limit, offset)
    return JsonResponse(
        {
            "course": course.slug,
            "cohort_key": cohort.external_key,
            "total": total,
            "limit": limit,
            "offset": offset,
            "members": members,
        }
    )


def _roster_related(user_ids, activity):
    tiers = _effective_tiers(user_ids)
    crm = {record.user_id: record for record in CRMRecord.objects.filter(user_id__in=user_ids)}
    profiles = {}
    queryset = AvailabilityProfile.objects.filter(user_id__in=user_ids).prefetch_related("windows")
    for profile in queryset:
        profiles[profile.user_id] = profile
    pod_ids = {}
    for user_id, pod_id in (
        PodMembership.objects.filter(
            user_id__in=user_ids,
            **activity.pod_filter("pod__"),
        )
        .values_list("user_id", "pod_id")
        .order_by("pod_id")
    ):
        pod_ids.setdefault(user_id, []).append(pod_id)
    return tiers, crm, profiles, pod_ids


def _crm_data(record):
    if record is None:
        return None
    return {
        "status": record.status,
        "persona": record.persona,
        "summary": record.summary,
        "next_steps": record.next_steps,
    }


def _availability_data(profile):
    if profile is None:
        return None
    windows = [
        {
            "weekday": window.weekday,
            "start": format_minute(window.start_minute),
            "end": format_minute(window.end_minute),
            "preference": window.preference,
        }
        for window in profile.windows.all()
    ]
    return {
        "timezone": profile.timezone,
        "updated_at": isoformat_or_none(profile.updated_at),
        "windows": windows,
    }


def _roster_member(enrollment, related):
    tiers, crm, profiles, pod_ids = related
    user = enrollment.user
    return {
        "email": user.email,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "tier": tiers.get(user.pk, "free"),
        "tags": list(user.tags or []),
        "preferred_timezone": user.preferred_timezone or "",
        "slack_member": bool(user.slack_member),
        "has_slack_user_id": bool(user.slack_user_id),
        "enrolled_at": isoformat_or_none(enrollment.enrolled_at),
        "crm": _crm_data(crm.get(user.pk)),
        "availability": _availability_data(profiles.get(user.pk)),
        "pod_ids": pod_ids.get(user.pk, []),
    }


def _roster_page(enrollments, activity, limit, offset):
    """``(total, members)`` for one activity-scoped roster page."""
    total = enrollments.count()
    page = list(enrollments[offset : offset + limit])
    related = _roster_related([enrollment.user_id for enrollment in page], activity)
    members = []
    for enrollment in page:
        members.append(_roster_member(enrollment, related))
    return total, members


@token_required(structured_errors=True)
@csrf_exempt
@require_methods("GET", structured_errors=True)
@openapi_spec(
    tag=TAG,
    summary="Sprint roster for pairing (staff-only)",
    methods={
        "GET": {
            "summary": "List a sprint roster with CRM, availability and pods (staff-only)",
            "description": (
                "Every user with a ``SprintEnrollment`` in the sprint (and only "
                "them), ordered by enrollment time, in the same member shape as "
                "the cohort roster (``GET /api/courses/<slug>/cohorts/<key>/members``). "
                "``enrolled_at`` comes from the sprint enrollment. ``pod_ids`` are "
                "the sprint study groups the user is in."
            ),
            "query": {
                "limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT, "default": DEFAULT_LIMIT},
                "offset": {"type": "integer", "minimum": 0, "default": 0},
            },
            "responses": {
                200: {
                    "description": "Roster page.",
                    "example": {
                        "sprint": "may-2026",
                        "total": 1,
                        "limit": DEFAULT_LIMIT,
                        "offset": 0,
                        "members": [_ROSTER_EXAMPLE],
                    },
                },
                401: {
                    "description": "Missing or invalid staff token.",
                    "example": {"error": "Authentication token required", "code": "authentication_required"},
                },
                404: {"description": "Unknown sprint.", "example": _ERR_UNKNOWN_SPRINT},
                422: {
                    "description": "Bad limit or offset.",
                    "example": {
                        "error": "limit must be between 1 and 500; offset must be 0 or more",
                        "code": "validation_error",
                    },
                },
            },
        },
    },
)
def sprint_roster(request, slug):
    """``GET /api/sprints/<slug>/members`` (issue #1921)."""
    sprint = Sprint.objects.filter(slug=slug).first()
    if sprint is None:
        return _unknown_sprint()
    limit, offset, error = _parse_limit(request, DEFAULT_LIMIT, MAX_LIMIT)
    if error is not None:
        return error
    enrollments = SprintEnrollment.objects.filter(sprint=sprint).select_related("user").order_by("enrolled_at", "pk")
    total, members = _roster_page(enrollments, sprint_activity(sprint), limit, offset)
    return JsonResponse(
        {
            "sprint": sprint.slug,
            "total": total,
            "limit": limit,
            "offset": offset,
            "members": members,
        }
    )
