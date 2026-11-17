"""Staff read-only course lookup endpoints (id, slug, peer review config)."""

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from api.openapi import openapi_spec
from api.safety import error_response
from api.utils import require_methods
from content.models import Cohort, Course, CourseProject

_COURSE_SUMMARY_EXAMPLE = {
    "id": 7,
    "slug": "ai-buildcamp",
    "title": "AI Buildcamp",
    "status": "published",
    "peer_review_enabled": True,
    "peer_review_count": 3,
    "peer_review_deadline_days": 7,
}


def _serialize_course_summary(course):
    return {
        "id": course.pk,
        "slug": course.slug,
        "title": course.title,
        "status": course.status,
        "peer_review_enabled": course.peer_review_enabled,
        "peer_review_count": course.peer_review_count,
        "peer_review_deadline_days": course.peer_review_deadline_days,
    }


def _serialize_cohort_brief(cohort):
    return {
        "id": cohort.pk,
        "external_key": cohort.external_key,
        "name": cohort.name,
        "mode": cohort.mode,
        "is_active": cohort.is_active,
    }


def _serialize_project_brief(project):
    cohort_key = None
    if project.cohort_id is not None:
        cohort_key = project.cohort.external_key
    return {
        "id": project.pk,
        "slug": project.slug,
        "title": project.title,
        "cohort_key": cohort_key,
        "state": project.state,
        "state_label": project.get_state_display(),
    }


@token_required
@csrf_exempt
@require_methods("GET")
@openapi_spec(
    tag="Courses",
    summary="List courses with numeric id and slug (staff-only)",
    methods={
        "GET": {
            "summary": "List courses (staff-only)",
            "description": (
                "Return courses with their numeric ``id``, ``slug``, ``title``, "
                "``status``, and peer-review defaults. Optional ``status`` query "
                "parameter filters by course status (e.g. ``published``)."
            ),
            "parameters": [
                {
                    "name": "status",
                    "in": "query",
                    "required": False,
                    "schema": {"type": "string"},
                    "description": "Filter by course status (e.g. published, draft).",
                },
            ],
            "responses": {
                200: {
                    "description": "List of courses.",
                    "example": {"courses": [_COURSE_SUMMARY_EXAMPLE]},
                },
            },
        },
    },
)
def courses_collection(request):
    """``GET /api/courses``."""
    queryset = Course.objects.order_by("pk")
    status_filter = (request.GET.get("status") or "").strip()
    if status_filter:
        queryset = queryset.filter(status=status_filter)
    courses = [_serialize_course_summary(c) for c in queryset]
    return JsonResponse({"courses": courses}, status=200)


@token_required
@csrf_exempt
@require_methods("GET")
@openapi_spec(
    tag="Courses",
    summary="Get one course by slug with numeric id, cohorts, and projects (staff-only)",
    methods={
        "GET": {
            "summary": "Get a course by slug (staff-only)",
            "description": (
                "Look up a course by ``slug`` and return its numeric ``id``, "
                "peer-review settings, cohorts, and project attempts."
            ),
            "responses": {
                200: {
                    "description": "Course detail.",
                    "example": {
                        **_COURSE_SUMMARY_EXAMPLE,
                        "cohorts": [
                            {
                                "id": 12,
                                "external_key": "4",
                                "name": "Cohort 4",
                                "mode": "cohort",
                                "is_active": True,
                            },
                        ],
                        "projects": [
                            {
                                "id": 45,
                                "slug": "attempt-1",
                                "title": "Project Attempt 1",
                                "cohort_key": "4",
                                "state": "PR",
                                "state_label": "Peer reviewing",
                            },
                        ],
                    },
                },
                404: {
                    "description": "Course not found.",
                    "example": {
                        "error": "Course not found",
                        "code": "unknown_course",
                    },
                },
            },
        },
    },
)
def course_detail(request, slug):
    """``GET /api/courses/<slug>``."""
    course = Course.objects.filter(slug=slug).first()
    if course is None:
        return error_response("Course not found", "unknown_course", status=404)
    cohorts = Cohort.objects.filter(course=course).order_by("-start_date", "pk")
    projects = (
        CourseProject.objects.filter(course=course)
        .select_related("cohort")
        .order_by("submission_due_at", "pk")
    )
    payload = _serialize_course_summary(course)
    payload["cohorts"] = [_serialize_cohort_brief(c) for c in cohorts]
    payload["projects"] = [_serialize_project_brief(p) for p in projects]
    return JsonResponse(payload, status=200)
