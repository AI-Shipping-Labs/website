"""Staff peer-review state and assignment endpoints for a course."""

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from api.openapi import openapi_spec
from api.safety import error_response
from api.utils import body_must_be_object_response, parse_json_body, require_methods
from api.views.course_peer_review_state import (
    _PROJECT_EXAMPLE,
    _annotated_projects,
    _resolve_scope,
    _serialize_project,
)
from api.views.course_peer_review_state import (
    course_peer_reviews as course_peer_reviews,
)
from content.models import Course, CourseProject
from content.services.peer_review_service import PeerReviewService


def _parse_optional_body(request):
    """Parse an optional JSON object body; allow empty body as {}."""
    if not request.body or not request.body.strip():
        return {}, None
    body, err = parse_json_body(request)
    if err is not None:
        return None, err
    if not isinstance(body, dict):
        return None, body_must_be_object_response()
    return body, None


@token_required
@csrf_exempt
@require_methods("POST")
@openapi_spec(
    tag="Course Peer Reviews",
    summary="Create round-robin peer review assignments (staff-only)",
    methods={
        "POST": {
            "summary": "Create peer review assignments (staff-only)",
            "description": (
                "Same behavior as Studio's 'Create review assignments' button: "
                "forms round-robin review assignments for waiting submissions, "
                "moves eligible dated project attempts to 'Peer reviewing', and "
                "sends on-site notifications and coursework.review_assigned emails. "
                "Idempotent: running twice creates no duplicate assignments or emails. "
                "Optionally scope to one ``cohort`` (external key) and/or ``project`` (slug)."
            ),
            "request_body": {
                "properties": {
                    "cohort": {
                        "type": "string",
                        "description": "Optional cohort external key (e.g. 4).",
                    },
                    "project": {
                        "type": "string",
                        "description": "Optional project attempt slug (e.g. attempt-1).",
                    },
                },
            },
            "responses": {
                200: {
                    "description": "Assignment summary and updated project state.",
                    "example": {
                        "course_id": 7,
                        "course": "ai-buildcamp",
                        "batched": 9,
                        "reviews_assigned": 27,
                        "projects": [_PROJECT_EXAMPLE],
                    },
                },
                404: {
                    "description": "Course, cohort, or project not found.",
                    "example": {
                        "error": "Course not found",
                        "code": "unknown_course",
                    },
                },
                422: {
                    "description": "Peer review is disabled for this course.",
                    "example": {
                        "error": "Peer review is not enabled for this course.",
                        "code": "peer_review_disabled",
                    },
                },
            },
        },
    },
)
def course_peer_review_assignments(request, slug):
    """``POST /api/courses/<slug>/peer-reviews/assignments``."""
    course = Course.objects.filter(slug=slug).first()
    if course is None:
        return error_response("Course not found", "unknown_course", status=404)
    if not course.peer_review_enabled:
        return error_response(
            "Peer review is not enabled for this course.",
            "peer_review_disabled",
            status=422,
        )
    body, err = _parse_optional_body(request)
    if err is not None:
        return err
    cohort_key = str(body.get("cohort") or "").strip()
    project_slug = str(body.get("project") or "").strip()
    cohort, project, scope_err = _resolve_scope(course, cohort_key, project_slug)
    if scope_err is not None:
        return scope_err
    result = _run_assignment(course, cohort, project)
    projects = _annotated_projects(course, cohort, project)
    return JsonResponse({
        "course_id": course.pk,
        "course": course.slug,
        "batched": result["batched"],
        "reviews_assigned": result["reviews_assigned"],
        "projects": [_serialize_project(course, p) for p in projects],
    }, status=200)


def _run_assignment(course, cohort, project):
    if project is not None:
        return PeerReviewService.form_batches_for_project(project)
    if cohort is not None:
        summary = {"batched": 0, "reviews_assigned": 0}
        for item in CourseProject.objects.filter(course=course, cohort=cohort):
            part = PeerReviewService.form_batches_for_project(item)
            summary["batched"] += part["batched"]
            summary["reviews_assigned"] += part["reviews_assigned"]
        return summary
    return PeerReviewService.form_batches_for_course(course)
