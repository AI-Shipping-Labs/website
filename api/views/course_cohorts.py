"""Staff cohort endpoints for a course.

- ``GET /api/courses/<slug>/cohorts`` -- every cohort of the course with
  its linked event series, enrollment count and event-series ``warnings``.
- ``PATCH /api/courses/<slug>/cohorts/<key>`` -- relink the cohort's
  ``event_series`` (by id or slug; a dated cohort cannot be unlinked)
  and/or change its ``start_date`` / ``end_date``. ``<key>`` is the cohort's external key
  (the ``?cohort=`` value, e.g. ``4``), matched case-insensitively.

Written after a production incident where every Cohort 4 session showed
"Not scheduled" because the cohort was not linked to its office-hours
series and no API exposed cohorts. The ``warnings`` come from
``content.services.course_cohorts.cohort_series_warnings`` -- the same
owner Studio's cohort pages and dashboard health item read.
"""

import datetime

from django.core.exceptions import ValidationError
from django.db.models import Count
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from api.openapi import openapi_spec
from api.safety import error_response
from api.utils import body_must_be_object_response, parse_json_body, require_methods, validation_response
from content.models import Cohort, Course
from content.models.cohort import COHORT_MODE_COHORT
from content.services.course_cohorts import cohort_series_warnings, get_course_cohort_by_key
from events.models import EventSeries
from events.models.event import PUBLIC_EVENT_STATUSES

_WRITABLE_FIELDS = ('event_series', 'start_date', 'end_date')

_COHORT_EXAMPLE = {
    "id": 12,
    "external_key": "4",
    "name": "Cohort 4",
    "mode": "cohort",
    "is_active": True,
    "start_date": "2026-09-14",
    "end_date": "2026-11-09",
    "event_series": {
        "id": 5,
        "slug": "buildcamp-office-hours-cohort-4",
        "name": "Buildcamp office hours - Cohort 4",
        "event_count": 9,
        "published_event_count": 9,
    },
    "enrollment_count": 42,
    "warnings": [],
}

_WARNING_EXAMPLE = {
    "code": "no_event_series",
    "message": (
        'No event series is linked, so every live session in this cohort '
        'shows "Not scheduled".'
    ),
}


def _unknown_course_response():
    return error_response('Course not found', 'unknown_course', status=404)


def _serialize_series(series):
    if series is None:
        return None
    statuses = list(series.events.values_list('status', flat=True))
    return {
        'id': series.pk,
        'slug': series.slug,
        'name': series.name,
        'event_count': len(statuses),
        'published_event_count': sum(
            1 for status in statuses if status in PUBLIC_EVENT_STATUSES
        ),
    }


def _serialize_cohort(cohort, warnings):
    return {
        'id': cohort.pk,
        'external_key': cohort.external_key,
        'name': cohort.name,
        'mode': cohort.mode,
        'is_active': cohort.is_active,
        'start_date': cohort.start_date.isoformat() if cohort.start_date else None,
        'end_date': cohort.end_date.isoformat() if cohort.end_date else None,
        'event_series': _serialize_series(cohort.event_series),
        'enrollment_count': getattr(cohort, 'num_enrollments', None) or cohort.enrollment_count,
        'warnings': warnings,
    }


def _course_cohorts(course):
    return (
        Cohort.objects.filter(course=course)
        .select_related('event_series')
        .order_by('start_date', 'pk')
    )


def _resolve_series(raw):
    """Return ``(series, error_response)`` for an ``event_series`` value."""
    if raw is None:
        return None, None
    if isinstance(raw, bool) or not isinstance(raw, (int, str)):
        return None, error_response(
            'event_series must be an id, a slug, or null',
            'invalid_type',
            status=422,
            details={'field': 'event_series', 'expected': 'integer|string|null'},
        )
    series = None
    if isinstance(raw, int):
        series = EventSeries.objects.filter(pk=raw).first()
    else:
        value = raw.strip()
        series = EventSeries.objects.filter(slug=value).first()
        if series is None and value.isdigit():
            series = EventSeries.objects.filter(pk=int(value)).first()
    if series is None:
        return None, error_response(
            f'Unknown event series {raw!r}',
            'unknown_event_series',
            status=400,
            details={'field': 'event_series'},
        )
    return series, None


def _parse_date_field(data, field):
    """Return ``(date_or_None, error_details_or_None)``."""
    raw = data[field]
    if raw is None:
        return None, None
    if isinstance(raw, str):
        try:
            return datetime.date.fromisoformat(raw.strip()), None
        except ValueError:
            pass
    return None, {'field': field, 'expected': 'YYYY-MM-DD date or null'}


@token_required
@csrf_exempt
@require_methods('GET')
@openapi_spec(
    tag="Course Cohorts",
    summary="List a course's cohorts with event-series warnings",
    methods={
        "GET": {
            "summary": "List course cohorts (staff-only)",
            "description": (
                "Every cohort of the course (any course status), ordered "
                "by start date. ``event_series`` is the linked live-session "
                "series or ``null``. ``warnings`` lists event-series "
                "problems that make sessions render \"Not scheduled\" for "
                "a dated (``mode='cohort'``) cohort: ``no_event_series``, "
                "``empty_event_series`` (no published events) and "
                "``missing_session_positions`` (published events' "
                "``series_position`` values do not cover the course's "
                "session units' ``session_position`` values; carries "
                "``missing_positions``)."
            ),
            "responses": {
                200: {
                    "description": "Cohorts for the course.",
                    "example": {
                        "course": "ai-buildcamp",
                        "cohorts": [
                            _COHORT_EXAMPLE,
                            {
                                **_COHORT_EXAMPLE,
                                "id": 13,
                                "external_key": "5",
                                "name": "Cohort 5",
                                "event_series": None,
                                "enrollment_count": 0,
                                "warnings": [_WARNING_EXAMPLE],
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
def course_cohorts_collection(request, slug):
    """``GET /api/courses/<slug>/cohorts``."""
    course = Course.objects.filter(slug=slug).first()
    if course is None:
        return _unknown_course_response()
    cohorts = list(_course_cohorts(course).annotate(num_enrollments=Count('enrollments')))
    warnings = cohort_series_warnings(cohorts)
    return JsonResponse({
        'course': course.slug,
        'cohorts': [_serialize_cohort(c, warnings[c.pk]) for c in cohorts],
    }, status=200)


@token_required
@csrf_exempt
@require_methods('PATCH')
@openapi_spec(
    tag="Course Cohorts",
    summary="Update a cohort's event series or dates (staff-only)",
    methods={
        "PATCH": {
            "summary": "Update a cohort (staff-only)",
            "description": (
                "``<key>`` is the cohort's external key (the ``?cohort=`` "
                "value), case-insensitive. Partial update of "
                "``event_series`` (an event-series id or slug), "
                "``start_date`` and ``end_date`` (``YYYY-MM-DD``). Every "
                "dated cohort must keep a linked series, so "
                "``event_series: null`` on one returns 422 "
                "``event_series_required``. "
                "The cohort's model validation still applies (a dated "
                "cohort needs both dates; a self-paced cohort cannot have "
                "a series). Returns the updated cohort, including fresh "
                "``warnings``."
            ),
            "request_body": {
                "properties": {
                    "event_series": {
                        "type": ["integer", "string", "null"],
                        "description": (
                            "Event-series id or slug. null is rejected for a "
                            "dated cohort."
                        ),
                    },
                    "start_date": {"type": ["string", "null"], "format": "date"},
                    "end_date": {"type": ["string", "null"], "format": "date"},
                },
                "example": {"event_series": "buildcamp-office-hours-cohort-4"},
            },
            "responses": {
                200: {
                    "description": "Updated cohort.",
                    "example": _COHORT_EXAMPLE,
                },
                400: {
                    "description": "Unknown event series.",
                    "example": {
                        "error": "Unknown event series 'no-such-series'",
                        "code": "unknown_event_series",
                    },
                },
                404: {
                    "description": "Course or cohort not found.",
                    "example": {
                        "error": "Unknown cohort '9' for course 'ai-buildcamp'",
                        "code": "unknown_cohort",
                    },
                },
                422: {
                    "description": (
                        "Invalid field, bad date, model validation error, or "
                        "clearing a dated cohort's series."
                    ),
                    "example": {
                        "error": (
                            "A dated cohort must keep a linked event series; "
                            "link a different series instead of clearing it"
                        ),
                        "code": "event_series_required",
                    },
                },
            },
        },
    },
)
def course_cohort_detail(request, slug, key):
    """``PATCH /api/courses/<slug>/cohorts/<key>``."""
    course = Course.objects.filter(slug=slug).first()
    if course is None:
        return _unknown_course_response()
    cohort = get_course_cohort_by_key(course, key)
    if cohort is None:
        return error_response(
            f'Unknown cohort {key!r} for course {course.slug!r}',
            'unknown_cohort',
            status=404,
        )

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

    if 'event_series' in data:
        if data['event_series'] is None and cohort.mode == COHORT_MODE_COHORT:
            return error_response(
                'A dated cohort must keep a linked event series; link a '
                'different series instead of clearing it',
                'event_series_required',
                status=422,
                details={'field': 'event_series'},
            )
        series, series_error = _resolve_series(data['event_series'])
        if series_error is not None:
            return series_error
        cohort.event_series = series
    for field in ('start_date', 'end_date'):
        if field in data:
            value, date_error = _parse_date_field(data, field)
            if date_error is not None:
                return validation_response(date_error, message=f'Invalid {field}')
            setattr(cohort, field, value)

    try:
        cohort.full_clean()
    except ValidationError as exc:
        return validation_response({'messages': exc.messages}, message='; '.join(exc.messages))
    cohort.save()

    warnings = cohort_series_warnings([cohort])
    return JsonResponse(_serialize_cohort(cohort, warnings[cohort.pk]), status=200)
