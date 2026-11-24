"""Staff peer-review state and assignment endpoints for a course.

- ``GET /api/courses/<slug>/peer-reviews`` -- read back peer-review state,
  submission counts, assignment counts, completed reviews, and deadline for
  the course's project attempts (optionally filtered by ``cohort`` and/or
  ``project``).
- ``POST /api/courses/<slug>/peer-reviews/assignments`` -- create round-robin
  peer review assignments (same behavior as Studio's "Create review
  assignments" button: sets state to ``Peer reviewing``, sends on-site
  notifications and ``coursework.review_assigned`` emails, idempotent).
"""

from django.db.models import Count, Q
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from api.openapi import openapi_spec
from api.safety import error_response
from api.serializers.datetime import isoformat_or_none
from api.utils import require_methods
from api.views.course_peer_review_incoming import add_incoming_details
from content.models import Course, CourseProject, ProjectSubmission
from content.models.peer_review import SUBMISSION_STATUS_CHOICES
from content.services.course_cohorts import get_course_cohort_by_key

_PROJECT_EXAMPLE = {
    "id": 45,
    "slug": "attempt-1",
    "title": "Project Attempt 1",
    "cohort": {
        "id": 12,
        "external_key": "4",
        "name": "Cohort 4",
        "mode": "cohort",
    },
    "state": "PR",
    "state_label": "Peer reviewing",
    "submission_due_at": "2026-11-16T22:59:00+00:00",
    "review_due_at": "2026-11-23T22:59:00+00:00",
    "deadline": "2026-11-23T22:59:00+00:00",
    "peer_review_count": 3,
    "submissions_count": 9,
    "submissions_by_status": {
        "submitted": 0,
        "in_review": 9,
        "review_complete": 0,
        "certified": 0,
    },
    "assignments_count": 27,
    "completed_reviews_count": 0,
    "pending_reviews_count": 27,
}


def _resolve_scope(course, cohort_key, project_slug):
    """Resolve optional cohort and project filters; return (cohort, project, err)."""
    cohort = None
    if cohort_key:
        cohort = get_course_cohort_by_key(course, cohort_key)
        if cohort is None:
            return None, None, error_response(
                "Cohort not found", "unknown_cohort", status=404,
            )
    project = None
    if project_slug:
        qs = CourseProject.objects.filter(course=course, slug=project_slug)
        if cohort is not None:
            qs = qs.filter(cohort=cohort)
        project = qs.select_related("cohort").first()
        if project is None:
            return None, None, error_response(
                "Project attempt not found", "unknown_project", status=404,
            )
    return cohort, project, None


def _annotated_projects(course, cohort, project):
    """Query CourseProjects with aggregated submission and review counts."""
    qs = CourseProject.objects.filter(course=course).select_related("cohort")
    if cohort is not None:
        qs = qs.filter(cohort=cohort)
    if project is not None:
        qs = qs.filter(pk=project.pk)
    annotations = {
        "submissions_count": Count("submissions", distinct=True),
        "assignments_count": Count("submissions__reviews", distinct=True),
        "completed_reviews_count": Count(
            "submissions__reviews",
            filter=Q(submissions__reviews__is_complete=True),
            distinct=True,
        ),
    }
    for status_code, _label in SUBMISSION_STATUS_CHOICES:
        annotations[f"status_{status_code}"] = Count(
            "submissions",
            filter=Q(submissions__status=status_code),
            distinct=True,
        )
    return list(qs.annotate(**annotations).order_by("submission_due_at", "pk"))


def _serialize_cohort_ref(cohort):
    if cohort is None:
        return None
    return {
        "id": cohort.pk,
        "external_key": cohort.external_key,
        "name": cohort.name,
        "mode": cohort.mode,
    }


def _serialize_project(course, project):
    effective_count = course.peer_review_count
    if project.peer_review_count is not None:
        effective_count = project.peer_review_count
    assignments = project.assignments_count
    completed = project.completed_reviews_count
    by_status = {}
    for status_code, _label in SUBMISSION_STATUS_CHOICES:
        by_status[status_code] = getattr(project, f"status_{status_code}", 0)
    review_due = isoformat_or_none(project.review_due_at)
    return {
        "id": project.pk,
        "slug": project.slug,
        "title": project.title,
        "cohort": _serialize_cohort_ref(project.cohort),
        "state": project.state,
        "state_label": project.get_state_display(),
        "submission_due_at": isoformat_or_none(project.submission_due_at),
        "review_due_at": review_due,
        "deadline": review_due,
        "peer_review_count": effective_count,
        "submissions_count": project.submissions_count,
        "submissions_by_status": by_status,
        "assignments_count": assignments,
        "completed_reviews_count": completed,
        "pending_reviews_count": max(0, assignments - completed),
    }


@token_required
@csrf_exempt
@require_methods("GET")
@openapi_spec(
    tag="Course Peer Reviews",
    summary="Read peer-review state for a course (staff-only)",
    methods={
        "GET": {
            "summary": "Get peer-review state (staff-only)",
            "description": (
                "Return peer-review state, submission counts, assignment counts, "
                "completed review counts, and deadline for the course's project "
                "attempts. Filter with optional ``cohort`` (cohort external key) "
                "and ``project`` (project attempt slug) query parameters. Opt in to "
                "incoming submission breakdowns with details=1, student, or submission. "
                "Each review includes staff reviewer identity, status, criterion points/max, "
                "selected text, null criterion feedback (not stored), and overall feedback. "
                "preliminary_score sums exact criterion medians of valid completed reviews; "
                "pending drafts are excluded and no completed reviews yields total=null. "
                "Includes passing_score and a provisional threshold comparison, not a final pass."
            ),
            "query": {
                "cohort": {"type": "string", "description": "Cohort external key (e.g. 4)."},
                "project": {"type": "string", "description": "Project attempt slug (e.g. attempt-1)."},
                "details": {"type": "string", "description": "Set to 1 for paginated incoming submission details."},
                "student": {"type": "string", "description": "Student email/full-name substring; includes incoming details."},
                "submission": {"type": "integer", "description": "Submission ID within course/project/cohort scope; includes details."},
                "page": {"type": "integer", "description": "Incoming submissions page (20 submissions per page)."},
            },
            "responses": {
                200: {
                    "description": "Peer-review state for matching project attempts.",
                    "example": {
                        "course_id": 7,
                        "course": "ai-buildcamp",
                        "course_title": "AI Buildcamp",
                        "peer_review_enabled": True,
                        "peer_review_count": 3,
                        "peer_review_deadline_days": 7,
                        "projects": [_PROJECT_EXAMPLE],
                        "legacy_submissions_count": 0,
                    },
                },
                404: {
                    "description": "Course, cohort, or project not found.",
                    "example": {
                        "error": "Course not found",
                        "code": "unknown_course",
                    },
                },
            },
        },
    },
)
def course_peer_reviews(request, slug):
    """``GET /api/courses/<slug>/peer-reviews``."""
    course = Course.objects.filter(slug=slug).first()
    if course is None:
        return error_response("Course not found", "unknown_course", status=404)
    cohort_key = (request.GET.get("cohort") or "").strip()
    project_slug = (request.GET.get("project") or "").strip()
    cohort, project, err = _resolve_scope(course, cohort_key, project_slug)
    if err is not None:
        return err
    projects = _annotated_projects(course, cohort, project)
    legacy_count = ProjectSubmission.objects.filter(
        course=course, course_project__isnull=True,
    ).count()
    data = {
        "course_id": course.pk,
        "course": course.slug,
        "course_title": course.title,
        "peer_review_enabled": course.peer_review_enabled,
        "peer_review_count": course.peer_review_count,
        "peer_review_deadline_days": course.peer_review_deadline_days,
        "projects": [_serialize_project(course, p) for p in projects],
        "legacy_submissions_count": legacy_count,
    }
    return add_incoming_details(request, course, cohort, project, data)
