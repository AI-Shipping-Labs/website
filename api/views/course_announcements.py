"""Staff course-announcement endpoints (issue #1942).

- ``GET /api/courses/<slug>/announcements`` — list, newest first.
- ``POST /api/courses/<slug>/announcements`` — create (draft default).
- ``GET /api/courses/<slug>/announcements/<id>`` — detail.
- ``PATCH /api/courses/<slug>/announcements/<id>`` — update title/body/status
  and, while the announcement is a draft, its cohort.
- ``GET /api/courses/<slug>/announcements/<id>/recipients`` — read-only
  recipient preview: the exact count and cohort the first publish would
  email. Sends nothing.
Publishing can carry ``expected_recipient_count`` (the previewed count); a
mismatch refuses the publish with 409 so a stale preview never sends.
Every announcement targets one cohort: only that cohort's members see it and
only its active enrollments get the first-publish email. Unpublished courses
are addressable by slug. DELETE is not available.
"""

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from api.openapi import openapi_spec
from api.safety import error_response
from api.serializers.course_announcements import serialize_course_announcement
from api.serializers.datetime import isoformat_or_none
from api.utils import (
    body_must_be_object_response,
    delete_not_available_response,
    parse_json_body,
    require_methods,
    validation_response,
)
from content.models import Course
from content.models.cohort import Cohort
from content.models.course_announcement import CourseAnnouncement
from content.services.course_announcements import (
    recipient_enrollments,
    recipient_preview,
    save_announcement,
)
from content.services.course_cohorts import get_course_cohort_by_key

_WRITABLE_FIELDS = ('title', 'body', 'status', 'cohort', 'cohort_id')
# Request-only guard, never stored: see ``_recipient_count_mismatch``.
_CONTROL_FIELDS = ('expected_recipient_count',)
_ALLOWED_FIELDS = _WRITABLE_FIELDS + _CONTROL_FIELDS
_STATUSES = frozenset({
    CourseAnnouncement.STATUS_DRAFT,
    CourseAnnouncement.STATUS_PUBLISHED,
})

_ANNOUNCEMENT_EXAMPLE = {
    'id': 12,
    'course': 'ai-dev-tools',
    'cohort': {'id': 7, 'external_key': '4', 'name': 'Cohort 4'},
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


def _parse_expected_recipient_count(data):
    if 'expected_recipient_count' not in data:
        return None
    raw = data['expected_recipient_count']
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        return validation_response(
            {'field': 'expected_recipient_count', 'expected': 'integer >= 0'},
            message='expected_recipient_count must be a non-negative integer',
        )
    return None


def _recipient_count_mismatch(data, *, previous_status, status, cohort):
    """409 when a first publish would email a different count than previewed.

    Only checked when the request publishes a draft and the caller sent
    ``expected_recipient_count``; the count is the same query the email
    worker fans out to.
    """
    if 'expected_recipient_count' not in data:
        return None
    if status != CourseAnnouncement.STATUS_PUBLISHED:
        return None
    if previous_status == CourseAnnouncement.STATUS_PUBLISHED:
        return None
    expected = data['expected_recipient_count']
    actual = recipient_enrollments(cohort).count()
    if actual == expected:
        return None
    return error_response(
        f'Recipient count changed: expected {expected}, the cohort now has '
        f'{actual} recipients. Preview again before publishing.',
        'recipient_count_mismatch',
        status=409,
        details={'expected': expected, 'actual': actual},
    )


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


def _unknown_cohort_response(field, raw, course):
    return validation_response(
        {'field': field, 'value': raw},
        message=f'Unknown cohort {raw!r} for course {course.slug!r}',
    )


def _parse_cohort(course, data, *, required):
    """Resolve ``cohort`` (external key) or ``cohort_id`` within ``course``.

    Returns ``(cohort, error_response)``; ``(None, None)`` when neither key
    was sent and the cohort is not required.
    """
    has_key = 'cohort' in data
    has_id = 'cohort_id' in data
    if has_key and has_id:
        return None, validation_response(
            {'field': 'cohort', 'expected': 'cohort or cohort_id, not both'},
            message='Send either cohort or cohort_id, not both',
        )
    if not has_key and not has_id:
        if required:
            return None, validation_response(
                {'field': 'cohort', 'expected': 'cohort external key'},
                message='cohort is required',
            )
        return None, None
    if has_key:
        raw = data['cohort']
        if not isinstance(raw, str) or not raw.strip():
            return None, validation_response(
                {'field': 'cohort', 'expected': 'cohort external key'},
                message='cohort must be a cohort external key',
            )
        cohort = get_course_cohort_by_key(course, raw)
        if cohort is None:
            return None, _unknown_cohort_response('cohort', raw, course)
        return cohort, None
    raw = data['cohort_id']
    if isinstance(raw, bool) or not isinstance(raw, int):
        return None, validation_response(
            {'field': 'cohort_id', 'expected': 'integer'},
            message='cohort_id must be an integer',
        )
    cohort = Cohort.objects.filter(pk=raw, course=course).first()
    if cohort is None:
        return None, _unknown_cohort_response('cohort_id', raw, course)
    return cohort, None


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
                "first. Optional ``status=draft|published`` filters the list; "
                "optional ``cohort=<external key>`` keeps one cohort's rows."
            ),
            "query": {
                "status": {
                    "type": "string",
                    "enum": ["draft", "published"],
                    "required": False,
                    "description": "Filter by announcement status.",
                },
                "cohort": {
                    "type": "string",
                    "required": False,
                    "description": "Filter by cohort external key.",
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
                "Body: ``title``, ``body``, the target cohort as ``cohort`` "
                "(its external key, the ``?cohort=`` value) or ``cohort_id``, "
                "and optional ``status`` (``draft`` default). Only members "
                "of that cohort see the announcement. ``status=published`` "
                "uses the same first-publish email path as Studio: it emails "
                "only the cohort's active enrollments."
            ),
            "request_body": {
                "properties": {
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                    "cohort": {
                        "type": "string",
                        "description": (
                            "Cohort external key. Required unless "
                            "cohort_id is sent."
                        ),
                    },
                    "cohort_id": {
                        "type": "integer",
                        "description": "Cohort id, instead of cohort.",
                    },
                    "status": {
                        "type": "string",
                        "enum": ["draft", "published"],
                    },
                    "expected_recipient_count": {
                        "type": "integer",
                        "description": (
                            "Optional publish guard, never stored: the "
                            "recipient_count from the recipients preview. "
                            "A first publish whose real count differs is "
                            "refused with 409 recipient_count_mismatch."
                        ),
                    },
                },
                "example": {
                    "title": "Week 2 office hours moved",
                    "body": "**Tomorrow** we meet at 18:00 CET.",
                    "cohort": "4",
                    "status": "draft",
                },
            },
            "responses": {
                201: {
                    "description": "Created announcement.",
                    "example": _ANNOUNCEMENT_EXAMPLE,
                },
                404: {"description": "Course not found."},
                409: {
                    "description": (
                        "expected_recipient_count differs from the real "
                        "recipient count; nothing was saved or sent."
                    ),
                },
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
            .select_related('course', 'author', 'cohort')
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
        cohort_key = request.GET.get('cohort')
        if cohort_key:
            cohort = get_course_cohort_by_key(course, cohort_key)
            if cohort is None:
                return _unknown_cohort_response('cohort', cohort_key, course)
            rows = rows.filter(cohort=cohort)
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
    unknown = sorted(set(data) - set(_ALLOWED_FIELDS))
    if unknown:
        return validation_response({
            'field': unknown[0],
            'allowed': list(_ALLOWED_FIELDS),
        }, message=f'Unknown field(s): {", ".join(unknown)}')
    expected_error = _parse_expected_recipient_count(data)
    if expected_error is not None:
        return expected_error
    title, title_error = _parse_title(data.get('title'))
    if title_error is not None:
        return title_error
    body, body_error = _parse_body(data.get('body'))
    if body_error is not None:
        return body_error
    status, status_error = _parse_status(data.get('status'))
    if status_error is not None:
        return status_error
    cohort, cohort_error = _parse_cohort(course, data, required=True)
    if cohort_error is not None:
        return cohort_error
    mismatch = _recipient_count_mismatch(
        data,
        previous_status=CourseAnnouncement.STATUS_DRAFT,
        status=status,
        cohort=cohort,
    )
    if mismatch is not None:
        return mismatch
    announcement = CourseAnnouncement(course=course)
    save_announcement(
        announcement,
        title=title,
        body=body,
        status=status,
        author=request.user,
        cohort=cohort,
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
                "Partial update of ``title``, ``body``, ``status``, and the "
                "target cohort (``cohort`` external key or ``cohort_id``). "
                "The cohort can change only while the announcement is a "
                "draft, and publishing needs one. The first ``draft`` to "
                "``published`` transition emails the cohort's active "
                "enrollments only; later publishes do not."
            ),
            "request_body": {
                "properties": {
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                    "cohort": {
                        "type": "string",
                        "description": "Cohort external key.",
                    },
                    "cohort_id": {
                        "type": "integer",
                        "description": "Cohort id, instead of cohort.",
                    },
                    "status": {
                        "type": "string",
                        "enum": ["draft", "published"],
                    },
                    "expected_recipient_count": {
                        "type": "integer",
                        "description": (
                            "Optional publish guard, never stored: the "
                            "recipient_count from the recipients preview. "
                            "A first publish whose real count differs is "
                            "refused with 409 recipient_count_mismatch."
                        ),
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
                409: {
                    "description": (
                        "expected_recipient_count differs from the real "
                        "recipient count; nothing was saved or sent."
                    ),
                },
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
        .select_related('course', 'author', 'cohort')
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
    unknown = sorted(set(data) - set(_ALLOWED_FIELDS))
    if unknown:
        return validation_response({
            'field': unknown[0],
            'allowed': list(_ALLOWED_FIELDS),
        }, message=f'Unknown field(s): {", ".join(unknown)}')
    expected_error = _parse_expected_recipient_count(data)
    if expected_error is not None:
        return expected_error
    if not set(data) & set(_WRITABLE_FIELDS):
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
    cohort, cohort_error = _parse_cohort(course, data, required=False)
    if cohort_error is not None:
        return cohort_error
    if (
        cohort is not None
        and cohort.pk != announcement.cohort_id
        and announcement.status == CourseAnnouncement.STATUS_PUBLISHED
    ):
        return validation_response(
            {'field': 'cohort', 'expected': 'draft announcement'},
            message='Unpublish the announcement before changing its cohort',
        )
    if (
        status == CourseAnnouncement.STATUS_PUBLISHED
        and cohort is None
        and announcement.cohort_id is None
    ):
        return validation_response(
            {'field': 'cohort', 'expected': 'cohort external key'},
            message='Set a cohort before publishing',
        )
    mismatch = _recipient_count_mismatch(
        data,
        previous_status=announcement.status,
        status=status,
        cohort=cohort if cohort is not None else announcement.cohort,
    )
    if mismatch is not None:
        return mismatch
    save_announcement(
        announcement,
        title=title,
        body=body,
        status=status,
        author=request.user,
        cohort=cohort,
    )
    return JsonResponse(serialize_course_announcement(announcement), status=200)


def _parse_preview_cohort(request, course):
    """Optional ``?cohort=<key>`` / ``?cohort_id=<id>`` preview override."""
    key = request.GET.get('cohort')
    raw_id = request.GET.get('cohort_id')
    if key and raw_id:
        return None, validation_response(
            {'field': 'cohort', 'expected': 'cohort or cohort_id, not both'},
            message='Send either cohort or cohort_id, not both',
        )
    if key:
        cohort = get_course_cohort_by_key(course, key)
        if cohort is None:
            return None, _unknown_cohort_response('cohort', key, course)
        return cohort, None
    if raw_id:
        try:
            cohort_id = int(raw_id)
        except ValueError:
            return None, validation_response(
                {'field': 'cohort_id', 'expected': 'integer'},
                message='cohort_id must be an integer',
            )
        cohort = Cohort.objects.filter(pk=cohort_id, course=course).first()
        if cohort is None:
            return None, _unknown_cohort_response('cohort_id', raw_id, course)
        return cohort, None
    return None, None


_PREVIEW_EXAMPLE = {
    'announcement_id': 12,
    'course': 'ai-buildcamp',
    'status': 'draft',
    'cohort': {'id': 1, 'external_key': '4', 'name': 'Cohort 4'},
    'stored_cohort_id': 1,
    'recipient_count': 54,
    'course_active_enrollment_count': 167,
    'course_cohort_count': 5,
    'email_queued_at': None,
    'will_email': True,
    'blocked_reason': None,
}


@token_required
@csrf_exempt
@require_methods('GET')
@openapi_spec(
    tag="Course Announcements",
    summary="Preview who a course announcement would email",
    methods={
        "GET": {
            "summary": "Recipient preview for a course announcement (staff-only)",
            "description": (
                "Read-only: returns the exact number of learners the first "
                "publish would email (active enrollments of active users in "
                "the announcement's cohort) and the cohort's name. Sends "
                "nothing and changes nothing. ``cohort=<external key>`` or "
                "``cohort_id`` previews another cohort of the course (the one "
                "a publish request would set). ``will_email`` is false with "
                "``blocked_reason`` ``no_cohort``, ``already_published`` or "
                "``already_emailed`` when publishing would email nobody. "
                "``course_active_enrollment_count`` is the whole-course "
                "number for comparison; it is never an email audience."
            ),
            "query": {
                "cohort": {
                    "type": "string",
                    "required": False,
                    "description": "Preview this cohort external key instead.",
                },
                "cohort_id": {
                    "type": "integer",
                    "required": False,
                    "description": "Preview this cohort id instead.",
                },
            },
            "responses": {
                200: {
                    "description": "Recipient preview.",
                    "example": _PREVIEW_EXAMPLE,
                },
                404: {"description": "Course or announcement not found."},
                422: {"description": "Unknown cohort."},
            },
        },
    },
)
def course_announcement_recipients(request, slug, announcement_id):
    """``GET /api/courses/<slug>/announcements/<id>/recipients``."""
    course = _course(slug)
    if course is None:
        return _unknown_course_response()
    announcement = (
        CourseAnnouncement.objects
        .select_related('course', 'cohort')
        .filter(pk=announcement_id, course=course)
        .first()
    )
    if announcement is None:
        return _unknown_announcement_response()
    cohort, cohort_error = _parse_preview_cohort(request, course)
    if cohort_error is not None:
        return cohort_error
    preview = recipient_preview(announcement, cohort=cohort)
    preview['email_queued_at'] = isoformat_or_none(preview['email_queued_at'])
    return JsonResponse(preview, status=200)
