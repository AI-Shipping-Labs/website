"""OpenAPI contract for the study-group collection endpoint."""

from api.views.pod_api_examples import _POD_DETAIL_EXAMPLE, _POD_SUMMARY_EXAMPLE, _RESULTS_EXAMPLE
from api.views.pod_api_shared import _STATUS_VALUES, POD_LIST_DEFAULT_LIMIT, POD_LIST_MAX_LIMIT

COLLECTION_METHODS = {
    "GET": {
        "summary": "List pods (staff-only)",
        "description": (
            "Pod summaries (cohort and sprint pods), newest first. Filter by "
            "``course`` slug, ``cohort`` key (needs ``course``), ``sprint`` "
            "slug and ``status``. ``sprint`` cannot be combined with "
            "``course`` or ``cohort`` (422). Sprint pods carry ``sprint_slug`` "
            "and ``sprint_name`` with null cohort fields; cohort pods the reverse."
        ),
        "query": {
            "course": {"type": "string"},
            "cohort": {"type": "string"},
            "sprint": {"type": "string"},
            "status": {"type": "string", "enum": _STATUS_VALUES},
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": POD_LIST_MAX_LIMIT,
                "default": POD_LIST_DEFAULT_LIMIT,
            },
            "offset": {"type": "integer", "minimum": 0, "default": 0},
        },
        "responses": {
            200: {
                "description": "Pod page.",
                "example": {
                    "total": 1,
                    "limit": POD_LIST_DEFAULT_LIMIT,
                    "offset": 0,
                    "pods": [_POD_SUMMARY_EXAMPLE],
                },
            },
            401: {"description": "Missing or invalid staff token."},
            404: {
                "description": "Unknown course, cohort or sprint filter.",
                "example": {"error": "Course not found", "code": "unknown_course"},
            },
            422: {"description": "Bad status, limit or offset, or ``sprint`` combined with ``course``."},
        },
    },
    "POST": {
        "summary": "Create a pod (staff-only)",
        "description": (
            "Creates a pod in a dated cohort (``course_slug`` + "
            "``cohort_key``) or a sprint (``sprint_slug``) with "
            "``source=api``. Giving both ``sprint_slug`` and a course "
            "returns 422 with ``field: sprint_slug``; giving neither returns "
            "422 with ``field: course_slug``. The optional ``owner_email`` "
            "must be enrolled in the cohort or sprint and becomes the "
            "first member. ``member_emails`` are added without a size limit; "
            "``results`` sorts every email into ``added``, "
            "``not_enrolled``, ``unknown_user``, ``already_member`` and "
            "no capacity bucket. A self-paced cohort returns 422. "
            "Idempotent: when a non-archived pod with the same name "
            "(case-insensitive) already exists in the cohort or sprint, it is "
            "returned with 200 and only new emails are added; its "
            "settings and owner are left unchanged (use PATCH)."
        ),
        "request_body": {
            "required": ["name", "purpose"],
            "properties": {
                "course_slug": {"type": "string", "description": "With ``cohort_key``; omit for a sprint pod."},
                "cohort_key": {"type": "string"},
                "sprint_slug": {"type": "string", "description": "Create a sprint pod instead of a cohort pod."},
                "name": {"type": "string", "maxLength": 80},
                "purpose": {"type": "string", "maxLength": 160},
                "scheduling_note": {"type": "string", "maxLength": 500},
                "internal_notes": {"type": "string", "maxLength": 2000},
                "max_members": {"description": "Retired; writes return 422 validation_error."},
                "meeting_count": {"type": "integer", "minimum": 1, "maximum": 20},
                "meeting_minutes": {"type": "integer", "enum": [30, 45, 60, 90]},
                "owner_email": {"type": ["string", "null"]},
                "member_emails": {"type": "array", "items": {"type": "string"}},
            },
            "example": {
                "course_slug": "ai-buildcamp",
                "cohort_key": "4",
                "name": "Agents pod",
                "purpose": "Ship an agent demo",
                "owner_email": "anna@example.com",
                "member_emails": ["anna@example.com", "raj@example.com", "someone@example.com"],
            },
        },
        "responses": {
            200: {
                "description": "A pod with this name already exists in the cohort or sprint; new emails were added.",
                "example": {
                    **_POD_DETAIL_EXAMPLE,
                    "results": {
                        **_RESULTS_EXAMPLE,
                        "added": [],
                        "already_member": ["anna@example.com", "raj@example.com"],
                    },
                },
            },
            201: {"description": "Pod created.", "example": {**_POD_DETAIL_EXAMPLE, "results": _RESULTS_EXAMPLE}},
            400: {"description": "Invalid JSON or non-object body."},
            401: {"description": "Missing or invalid staff token."},
            404: {
                "description": "Unknown course, cohort or sprint.",
                "example": {"error": "Unknown cohort '9' for course 'ai-buildcamp'", "code": "unknown_cohort"},
            },
            422: {
                "description": (
                    "Validation error, including a self-paced cohort, an owner who is not enrolled, "
                    "or both ``sprint_slug`` and ``course_slug``."
                ),
                "example": {
                    "error": "Pods need a dated cohort. A self-paced cohort cannot have pods.",
                    "code": "validation_error",
                },
            },
        },
    },
}
