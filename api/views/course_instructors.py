"""Staff-token ordered course-instructor association API."""

from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from api.openapi import openapi_spec
from api.safety import error_response
from api.utils import (
    body_must_be_object_response,
    parse_json_body,
    require_methods,
    validation_response,
)
from content.models import Course, CourseInstructor
from content.services.course_instructors import (
    CourseInstructorError,
    replace_course_instructors,
)


def _serialize(course):
    rows = (
        CourseInstructor.objects.filter(course=course)
        .select_related("instructor")
        .order_by("position", "pk")
    )
    return {
        "instructors": [
            {
                "instructor_id": row.instructor.instructor_id,
                "name": row.instructor.name,
                "position": row.position,
            }
            for row in rows
        ]
    }


@token_required
@csrf_exempt
@require_methods("GET", "PUT")
@openapi_spec(
    tag="Courses",
    methods={
        "GET": {
            "summary": "List ordered course instructors",
            "path_params": {"slug": {"type": "string", "required": True}},
            "responses": {
                200: {
                    "description": "Privacy-limited ordered instructor associations.",
                    "example": {
                        "instructors": [
                            {"instructor_id": "ada", "name": "Ada", "position": 0}
                        ]
                    },
                },
                401: {"description": "Missing or invalid staff token."},
                404: {"description": "Course not found."},
            },
        },
        "PUT": {
            "summary": "Replace ordered course instructors",
            "description": (
                "Atomically replaces instructors for a database-managed course "
                "and normalizes positions to contiguous zero-based values."
            ),
            "path_params": {"slug": {"type": "string", "required": True}},
            "request_body": {
                "type": "object",
                "required": ["instructor_ids"],
                "properties": {
                    "instructor_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    }
                },
                "example": {"instructor_ids": ["ada", "grace"]},
            },
            "responses": {
                200: {
                    "description": "Normalized ordered associations.",
                    "example": {
                        "instructors": [
                            {"instructor_id": "ada", "name": "Ada", "position": 0}
                        ]
                    },
                },
                401: {"description": "Missing or invalid staff token."},
                404: {"description": "Course not found."},
                409: {"description": "Course instructors are source-owned."},
                422: {"description": "Unknown, duplicate, or malformed ids."},
            },
        },
    },
)
def course_instructors(request, slug):
    course = get_object_or_404(Course, slug=slug)
    if request.method == "GET":
        return JsonResponse(_serialize(course))

    data, parse_error = parse_json_body(request)
    if parse_error:
        return parse_error
    if not isinstance(data, dict):
        return body_must_be_object_response()
    if set(data) != {"instructor_ids"}:
        details = {}
        if "instructor_ids" not in data:
            details["instructor_ids"] = "This field is required."
        for field in sorted(set(data) - {"instructor_ids"}):
            details[field] = "Unknown field."
        return validation_response(details)
    try:
        replace_course_instructors(course, data["instructor_ids"])
    except CourseInstructorError as exc:
        if exc.code == "source_owned":
            return error_response(str(exc), exc.code, status=409)
        return error_response(
            str(exc),
            exc.code,
            status=422,
            details={"instructor_ids": str(exc)},
        )
    return JsonResponse(_serialize(course))
