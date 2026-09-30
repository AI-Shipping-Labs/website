"""Staff read-only ``GET /api/courses/<slug>/coursework-inventory`` (#1696).

Counts a course's legacy project, submission, peer-review and certificate
rows before they move onto ``community_base.coursework``. The counting lives
in ``content.services.coursework_inventory`` so the migration's dry run
reports the same numbers.
"""

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from api.openapi import openapi_spec
from api.safety import error_response
from api.utils import require_methods
from content.models import Course
from content.services.coursework_inventory import build_coursework_inventory

_COHORT_REF_EXAMPLE = {
    "cohort_id": 12,
    "external_key": "4",
    "name": "Cohort 4",
    "mode": "cohort",
}

_INVENTORY_EXAMPLE = {
    "course": "ai-buildcamp",
    "cohorts": {
        "total": 2,
        "by_mode": {"cohort": 1, "self_paced": 1},
        "items": [{
            **_COHORT_REF_EXAMPLE,
            "is_active": True,
            "start_date": "2026-09-14",
            "end_date": "2026-11-09",
            "enrollment_count": 57,
            "project_count": 2,
            "submission_count": 0,
            "has_projects": True,
        }],
    },
    "enrollments": {
        "cohorts_with_projects": 1,
        "cohort_enrollments": 57,
        "by_cohort": [{**_COHORT_REF_EXAMPLE, "count": 57}],
    },
    "projects": {
        "total": 3,
        "null_cohort": 1,
        "by_cohort": [{**_COHORT_REF_EXAMPLE, "count": 2}],
        "unscoped_without_submissions": {"count": 1, "slugs": ["preview"]},
        "items": [{
            "id": 7,
            "slug": "attempt-1",
            "title": "Attempt 1",
            "cohort_id": 12,
            "submission_due_at": "2026-11-16T23:59:00+00:00",
            "review_due_at": "2026-11-23T23:59:00+00:00",
            "peer_review_count": None,
            "submission_count": 0,
        }],
    },
    "submissions": {
        "total": 0,
        "by_status": {
            "submitted": 0,
            "in_review": 0,
            "review_complete": 0,
            "certified": 0,
        },
        "without_course_project": 0,
        "description": {"blank": 0, "non_blank": 0},
    },
    "reviews": {
        "total": 0,
        "complete": 0,
        "incomplete": 0,
        "scored": 0,
        "unscored": 0,
        "reviewers_without_submission": {"pairs": 0, "reviews": 0},
    },
    "batches": {
        "legacy_pooled_groups": 0,
        "submissions_in_groups": 0,
        "groups": [],
    },
    "certificates": {"total": 0, "with_submission": 0, "revoked": 0},
}


@token_required
@csrf_exempt
@require_methods('GET')
@openapi_spec(
    tag="Course Cohorts",
    summary="Count a course's legacy coursework rows (staff-only)",
    methods={
        "GET": {
            "summary": "Coursework inventory (staff-only, read-only)",
            "description": (
                "Counts the course's current legacy project rows, the "
                "baseline for moving projects onto "
                "``community_base.coursework``. Sections: ``cohorts`` "
                "(per mode, with enrollment, project and submission counts), "
                "``enrollments`` (``CohortEnrollment`` rows in cohorts that "
                "have projects; a self-paced cohort has projects when an "
                "unscoped attempt exists), ``projects`` (``CourseProject`` "
                "per cohort, ``null_cohort`` counted separately, and "
                "unscoped attempts with zero submissions), ``submissions`` "
                "(per ``status``, ``course_project=NULL``, blank versus "
                "non-blank ``description``), ``reviews`` (complete or "
                "incomplete, scored or null, and reviewers with no "
                "submission in the reviewed attempt as distinct "
                "``pairs`` and ``reviews``), ``batches`` (legacy "
                "self-paced pooled groups sharing ``batch_assigned_at``) "
                "and ``certificates`` (``with_submission`` has the "
                "submission FK). Writes nothing."
            ),
            "responses": {
                200: {
                    "description": "Inventory for the course.",
                    "example": _INVENTORY_EXAMPLE,
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
def course_coursework_inventory(request, slug):
    """``GET /api/courses/<slug>/coursework-inventory``."""
    course = Course.objects.filter(slug=slug).first()
    if course is None:
        return error_response('Course not found', 'unknown_course', status=404)
    return JsonResponse(build_coursework_inventory(course), status=200)
