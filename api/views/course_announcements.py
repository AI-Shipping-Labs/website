"""Staff course-announcement endpoints (issue #1942).

- ``GET /api/courses/<slug>/announcements`` — list, newest first.
- ``POST /api/courses/<slug>/announcements`` — create (draft default).
- ``GET /api/courses/<slug>/announcements/<id>`` — detail.
- ``PATCH /api/courses/<slug>/announcements/<id>`` — update title/body/status.
Unpublished courses are addressable by slug. DELETE is not available.
"""

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from api.openapi import openapi_spec
from api.safety import error_response
from api.serializers.course_announcements import serialize_course_announcement
from api.utils import (
    body_must_be_object_response,
    delete_not_available_response,
    parse_json_body,
    require_methods,
    validation_response,
)
from content.models import Course
from content.models.course_announcement import CourseAnnouncement
from content.services.course_announcements import save_announcement

_WRITABLE_FIELDS = ('title', 'body', 'status')
_STATUSES = frozenset({
    CourseAnnouncement.STATUS_DRAFT,
    CourseAnnouncement.STATUS_PUBLISHED,
})

_ANNOUNCEMENT_EXAMPLE = {
    'id': 12,
    'course': 'ai-dev-tools',
    'title': 'Week 2 office hours moved',
    'body': '**Tomorrow** we meet at 18:00 CET.',
    'author_email': 'admin@test.com',
    'status': 'published',
    'published_at': '2026-10-12T12:00:00+00:00',
    'email_queued_at': '2026-10-12T12:00:01+00:00',
    'created_at': '2026-10-12T11:50:00+00:00',
    'updated_at': '2026-10-12T12:00:00+00:00',
}

DELETE_NOT_AVAILABLE_MESSAGE = (
    'Course announcement deletion is not available through the API; '
    'unpublish in Studio instead.'
)


def _unknown_course_response():
    return error_response('Course not found', 'unknown_course', status=404)


def _unknown_announcement_response():
    return error_response(
        'Announcement not found', 'unknown_announcement', status=404,
    )


def _course(slug):
    return Course.objects.filter(slug=slug).first()


def _parse_status(raw, *, required=False):
    if raw is None:
        if required:
            return None, validation_response(
                {'field': 'status', 'expected': 'draft|published'},
                message='status is required',
            )
        return CourseAnnouncement.STATUS_DRAFT, None
    if not isinstance(raw, str) or raw not in _STATUSES:
        return None, validation_response(
            {'field': 'status', 'expected': 'draft|published'},
            message='status must be draft or published',
        )
    return raw, None


def _parse_title(raw, *, required=True):
    if raw is None:
        if required:
            return None, validation_response(
                {'field': 'title'},
                message='title is required',
            )
        return None, None
    if not isinstance(raw, str):
        return None, validation_response(
            {'field': 'title', 'expected': 'string'},
            message='title must be a string',
        )
    title = raw.strip()
    if required and not title:
        return None, validation_response(
            {'field': 'title'},
            message='title is required',
        )
    if len(title) > 200:
        return None, validation_response(
            {'field': 'title', 'expected': 'max_length=200'},
            message='title must be 200 characters or fewer',
        )
    return title, None


def _parse_body(raw, *, required=True):
    if raw is None:
        if required:
            return None, validation_response(
                {'field': 'body'},
                message='body is required',
            )
        return None, None
    if not isinstance(raw, str):
        return None, validation_response(
            {'field': 'body', 'expected': 'string'},
            message='body must be a string',
        )
    return raw, None


@token_required
@csrf_exempt
@require_methods('GET', 'POST', 'DELETE')
@openapi_spec(
    tag="Course Announcements",
    summary="List or create course announcements",
    methods={
        "GET": {
            "summary": "List course announcements (staff-only)",
            "description": (
                "Announcements for this course (any course status), newest "
                "first. Optional ``status=draft|published`` filters the list."
            ),
            "query": {
                "status": {
                    "type": "string",
                    "enum": ["draft", "published"],
                    "required": False,
                    "description": "Filter by announcement status.",
                },
            },
            "responses": {
                200: {
                    "description": "Announcements for the course.",
                    "example": {"announcements": [_ANNOUNCEMENT_EXAMPLE]},
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
        "POST": {
            "summary": "Create a course announcement (staff-only)",
            "description": (
                "Body: ``title``, ``body``, optional ``status`` "
                "(``draft`` default). ``status=published`` uses the same "
                "first-publish email path as Studio."
            ),
            "request_body": {
                "properties": {
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                    "status": {
                        "type": "string",
                        "enum": ["draft", "published"],
                    },
                },
                "example": {
                    "title": "Week 2 office hours moved",
                    "body": "**Tomorrow** we meet at 18:00 CET.",
                    "status": "published",
                },
            },
            "responses": {
                201: {
                    "description": "Created announcement.",
                    "example": _ANNOUNCEMENT_EXAMPLE,
                },
                404: {"description": "Course not found."},
                422: {"description": "Validation error."},
            },
        },
        "DELETE": {
            "summary": "DELETE is not available on this route",
            "description": DELETE_NOT_AVAILABLE_MESSAGE,
            "responses": {
                405: {
                    "description": "Announcement deletion is not available.",
                    "example": {
                        "error": DELETE_NOT_AVAILABLE_MESSAGE,
                        "code": "course_announcement_delete_not_available",
                    },
                },
            },
        },
    },
)
def course_announcements_collection(request, slug):
    """``GET / POST /api/courses/<slug>/announcements``."""
    if request.method == 'DELETE':
        return delete_not_available_response(
            DELETE_NOT_AVAILABLE_MESSAGE,
            'course_announcement_delete_not_available',
        )
    course = _course(slug)
    if course is None:
        return _unknown_course_response()
    if request.method == 'GET':
        rows = (
            CourseAnnouncement.objects
            .filter(course=course)
            .select_related('course', 'author')
            .order_by('-published_at', '-created_at')
        )
        status = request.GET.get('status')
        if status:
            if status not in _STATUSES:
                return validation_response(
                    {'field': 'status', 'expected': 'draft|published'},
                    message='status must be draft or published',
                )
            rows = rows.filter(status=status)
        return JsonResponse({
            'announcements': [
                serialize_course_announcement(row) for row in rows
            ],
        }, status=200)

    data, parse_error = parse_json_body(request)
    if parse_error is not None:
        return parse_error
    if not isinstance(data, dict):
        return body_must_be_object_response()
    unknown = sorted(set(data) - set(_WRITABLE_FIELDS))
    if unknown:
        return validation_response({
            'field': unknown[0],
            'allowed': list(_WRITABLE_FIELDS),
        }, message=f'Unknown field(s): {", ".join(unknown)}')
    title, title_error = _parse_title(data.get('title'))
    if title_error is not None:
        return title_error
    body, body_error = _parse_body(data.get('body'))
    if body_error is not None:
        return body_error
    status, status_error = _parse_status(data.get('status'))
    if status_error is not None:
        return status_error
    announcement = CourseAnnouncement(course=course)
    save_announcement(
        announcement,
        title=title,
        body=body,
        status=status,
        author=request.user,
    )
    return JsonResponse(
        serialize_course_announcement(announcement),
        status=201,
    )


@token_required
@csrf_exempt
@require_methods('GET', 'PATCH', 'DELETE')
@openapi_spec(
    tag="Course Announcements",
    summary="Read or update one course announcement",
    methods={
        "GET": {
            "summary": "Get a course announcement (staff-only)",
            "responses": {
                200: {
                    "description": "Announcement detail.",
                    "example": _ANNOUNCEMENT_EXAMPLE,
                },
                404: {"description": "Course or announcement not found."},
            },
        },
        "PATCH": {
            "summary": "Update a course announcement (staff-only)",
            "description": (
                "Partial update of ``title``, ``body``, and ``status``. "
                "The first ``draft`` to ``published`` transition emails "
                "enrolled learners; later publishes do not."
            ),
            "request_body": {
                "properties": {
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                    "status": {
                        "type": "string",
                        "enum": ["draft", "published"],
                    },
                },
                "example": {"title": "Updated title", "body": "Updated body"},
            },
            "responses": {
                200: {
                    "description": "Updated announcement.",
                    "example": _ANNOUNCEMENT_EXAMPLE,
                },
                404: {"description": "Course or announcement not found."},
                422: {"description": "Validation error."},
            },
        },
        "DELETE": {
            "summary": "DELETE is not available on this route",
            "description": DELETE_NOT_AVAILABLE_MESSAGE,
            "responses": {
                405: {
                    "description": "Announcement deletion is not available.",
                    "example": {
                        "error": DELETE_NOT_AVAILABLE_MESSAGE,
                        "code": "course_announcement_delete_not_available",
                    },
                },
            },
        },
    },
)
def course_announcement_detail(request, slug, announcement_id):
    """``GET / PATCH /api/courses/<slug>/announcements/<id>``."""
    if request.method == 'DELETE':
        return delete_not_available_response(
            DELETE_NOT_AVAILABLE_MESSAGE,
            'course_announcement_delete_not_available',
        )
    course = _course(slug)
    if course is None:
        return _unknown_course_response()
    announcement = (
        CourseAnnouncement.objects
        .select_related('course', 'author')
        .filter(pk=announcement_id, course=course)
        .first()
    )
    if announcement is None:
        return _unknown_announcement_response()
    if request.method == 'GET':
        return JsonResponse(serialize_course_announcement(announcement), status=200)

    data, parse_error = parse_json_body(request)
    if parse_error is not None:
        return parse_error
    if not isinstance(data, dict):
        return body_must_be_object_response()
    unknown = sorted(set(data) - set(_WRITABLE_FIELDS))
    if unknown:
        return validation_response({
            'field': unknown[0],
            'allowed': list(_WRITABLE_FIELDS),
        }, message=f'Unknown field(s): {", ".join(unknown)}')
    if not data:
        return validation_response({
            'field': 'body',
            'allowed': list(_WRITABLE_FIELDS),
        }, message='Nothing to update')
    title = announcement.title
    body = announcement.body
    status = announcement.status
    if 'title' in data:
        title, title_error = _parse_title(data.get('title'))
        if title_error is not None:
            return title_error
    if 'body' in data:
        body, body_error = _parse_body(data.get('body'))
        if body_error is not None:
            return body_error
    if 'status' in data:
        status, status_error = _parse_status(data.get('status'), required=True)
        if status_error is not None:
            return status_error
    save_announcement(
        announcement,
        title=title,
        body=body,
        status=status,
        author=request.user,
    )
    return JsonResponse(serialize_course_announcement(announcement), status=200)
