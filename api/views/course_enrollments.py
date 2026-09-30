"""Course enrollment endpoints (issue #445).

Three endpoints, mirroring the sprint enrollment shape from #443:

- ``GET /api/courses/<slug>/enrollments`` — list active enrollments by
  default; ``?include_unenrolled=1`` includes soft-deleted rows. Staff
  sees all rows; non-staff sees only their own.
- ``POST /api/courses/<slug>/enrollments`` — staff-only bulk enroll
  with the four-bucket result shape. Body accepts either
  ``{"user_email": "..."}`` (single) or ``{"user_emails": [...]}``
  (bulk); the single form is normalised into a one-element list. Tier
  mismatches are flagged as ``under_tier`` (warning) but the enrollment
  is still created — staff are explicitly choosing to enroll the user.
- ``DELETE /api/courses/<slug>/enrollments/<email>`` — staff-only,
  idempotent unenroll. Soft-deletes via ``unenrolled_at`` (preserves
  history). Returns 204 whether or not a row was changed.

Idempotency comes from ``content.services.enrollment.ensure_enrollment``
and ``unenroll`` so the API path runs through the same logic the UI
flow uses.
"""

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models.functions import Lower
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from accounts.models import EmailAlias
from api.openapi import openapi_spec
from api.safety import error_response
from api.serializers.enrollments import serialize_course_enrollment
from api.utils import parse_json_body, require_methods
from api.views._permissions import bearer_is_admin
from content.access import can_access
from content.models import Course
from content.models.enrollment import SOURCE_ADMIN, Enrollment
from content.services.course_cohorts import (
    assign_cohort_enrollment,
    get_course_cohort_by_key,
)
from content.services.enrollment import (
    UNENROLL_CAUSE_STAFF,
    ensure_enrollment,
    unenroll,
)

_ENROLLMENT_EXAMPLE = {
    "user_email": "alice@example.com",
    "enrolled_at": "2026-04-15T12:00:00+00:00",
    "unenrolled_at": None,
    "source": "admin",
}

User = get_user_model()


def _normalize_emails(raw):
    """Trim, lowercase, deduplicate while preserving input order."""
    seen = set()
    out = []
    for email in raw:
        if not isinstance(email, str):
            continue
        cleaned = email.strip().lower()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        out.append(cleaned)
    return out


def _users_by_email(emails):
    """Map normalized emails to users: primary login first, then aliases.

    An address recorded as an ``EmailAlias`` resolves to its canonical
    account (same precedence as ``resolve_user_by_email``), so enrolling a
    member by a secondary address never reports them as unknown.
    """
    users_by_email = {
        u.email.lower(): u
        for u in User.objects.annotate(
            _email_lower=Lower('email'),
        ).filter(_email_lower__in=emails)
    }
    unmatched = [email for email in emails if email not in users_by_email]
    if unmatched:
        for alias in EmailAlias.objects.select_related('user').filter(
            email__in=unmatched,
        ):
            users_by_email[alias.email] = alias.user
    return users_by_email


def _get_published_course(slug):
    """Return the published Course or None."""
    return Course.objects.filter(slug=slug, status='published').first()


@token_required
@csrf_exempt
@require_methods('GET', 'POST')
@openapi_spec(
    tag="Course Enrollments",
    summary="List or bulk-enroll course members",
    methods={
        "GET": {
            "summary": "List course enrollments",
            "description": (
                "Active enrollments by default; "
                "``?include_unenrolled=1`` includes soft-deleted rows. "
                "Staff sees all rows; non-staff sees only their own."
            ),
            "query": {
                "include_unenrolled": {
                    "type": "string",
                    "enum": ["1"],
                    "required": False,
                    "description": (
                        "When ``1``, include soft-deleted (unenrolled) "
                        "rows."
                    ),
                },
            },
            "responses": {
                200: {
                    "description": "List of enrollments.",
                    "example": {"enrollments": [_ENROLLMENT_EXAMPLE]},
                },
                404: {
                    "description": "Course not found or unpublished.",
                    "example": {
                        "error": "Course not found",
                        "code": "unknown_course",
                    },
                },
            },
        },
        "POST": {
            "summary": "Bulk enroll users (staff-only)",
            "description": (
                "Accepts either ``{\"user_email\": \"...\"}`` (single) "
                "or ``{\"user_emails\": [...]}`` (bulk). Tier mismatches "
                "are flagged as ``under_tier`` (warning) but the "
                "enrollment is still created -- staff are explicitly "
                "choosing to enroll the user. Optional ``cohort`` is the "
                "cohort's external key (the ``?cohort=`` value, e.g. "
                "``4``): each known user also gets an idempotent "
                "``CohortEnrollment`` in that cohort (an existing "
                "self-paced membership is kept; the dated cohort wins on "
                "course Home), and the response adds "
                "``cohort_enrollments``."
            ),
            "request_body": {
                "properties": {
                    "user_email": {"type": "string", "format": "email"},
                    "user_emails": {
                        "type": "array",
                        "items": {"type": "string", "format": "email"},
                    },
                    "cohort": {
                        "type": "string",
                        "description": (
                            "Cohort external key under this course "
                            "(case-insensitive), e.g. ``4``."
                        ),
                    },
                },
                "example": {
                    "user_emails": [
                        "alice@example.com",
                        "bob@example.com",
                    ],
                },
            },
            "responses": {
                200: {
                    "description": "Bulk enroll summary.",
                    "example": {
                        "enrolled": 1,
                        "already_enrolled": 1,
                        "under_tier": [],
                        "unknown_emails": [],
                        "cohort_enrollments": [
                            {
                                "user_email": "alice@example.com",
                                "cohort": "4",
                                "cohort_name": "Cohort 4",
                                "created": True,
                            },
                        ],
                    },
                },
                400: {
                    "description": "Unknown ``cohort`` for this course.",
                    "example": {
                        "error": (
                            "Unknown cohort '9' for course 'ai-buildcamp'"
                        ),
                        "code": "unknown_cohort",
                    },
                },
                403: {
                    "description": "Non-staff bearer.",
                    "example": {
                        "error": "Bulk enrollment is staff-only",
                        "code": "forbidden_other_user_plan",
                    },
                },
                404: {"description": "Course not found."},
                422: {
                    "description": (
                        "Missing user_email / user_emails or bad type."
                    ),
                },
            },
        },
    },
)
def course_enrollments_collection(request, slug):
    """``GET / POST /api/courses/<slug>/enrollments``."""
    course = _get_published_course(slug)
    if course is None:
        return error_response(
            'Course not found', 'unknown_course', status=404,
        )

    if request.method == 'GET':
        qs = Enrollment.objects.filter(course=course).select_related('user')
        if request.GET.get('include_unenrolled') != '1':
            qs = qs.filter(unenrolled_at__isnull=True)
        if not bearer_is_admin(request.user):
            qs = qs.filter(user=request.user)
        return JsonResponse(
            {'enrollments': [serialize_course_enrollment(e) for e in qs]},
            status=200,
        )

    # POST -- staff only
    if not bearer_is_admin(request.user):
        return error_response(
            'Bulk enrollment is staff-only',
            'forbidden_other_user_plan',
            status=403,
        )

    data, parse_error = parse_json_body(request)
    if parse_error is not None:
        return parse_error
    if not isinstance(data, dict):
        return error_response(
            'Body must be a JSON object',
            'invalid_type',
            status=422,
            details={'field': 'body', 'expected': 'object'},
        )

    raw_single = data.get('user_email')
    raw_list = data.get('user_emails')
    if raw_single is None and raw_list is None:
        return error_response(
            'Missing required field: user_email or user_emails',
            'missing_field',
            status=422,
            details={'field': 'user_email_or_user_emails'},
        )

    combined = []
    if isinstance(raw_single, str):
        combined.append(raw_single)
    if isinstance(raw_list, list):
        combined.extend(raw_list)

    raw_cohort = data.get('cohort')
    cohort = None
    if raw_cohort is not None:
        if not isinstance(raw_cohort, (str, int)) or isinstance(raw_cohort, bool):
            return error_response(
                'cohort must be a string',
                'invalid_type',
                status=422,
                details={'field': 'cohort', 'expected': 'string'},
            )
        cohort_key = str(raw_cohort).strip()
        cohort = get_course_cohort_by_key(course, cohort_key)
        if cohort is None:
            return error_response(
                f'Unknown cohort {cohort_key!r} for course {course.slug!r}',
                'unknown_cohort',
                status=400,
                details={'field': 'cohort'},
            )

    emails = _normalize_emails(combined)
    enrolled = []
    already = []
    under_tier = []
    unknown = []
    cohort_enrollments = []

    with transaction.atomic():
        users_by_email = _users_by_email(emails)

        for email in emails:
            user = users_by_email.get(email)
            if user is None:
                unknown.append(email)
                continue
            _, created = ensure_enrollment(user, course, source=SOURCE_ADMIN)
            if created:
                enrolled.append(email)
            else:
                already.append(email)
            if not can_access(user, course):
                under_tier.append(email)
            if cohort is not None:
                _, cohort_created = assign_cohort_enrollment(user, cohort)
                cohort_enrollments.append({
                    'user_email': email,
                    'cohort': cohort.external_key,
                    'cohort_name': cohort.name,
                    'created': cohort_created,
                })

    body = {
        'enrolled': len(enrolled),
        'already_enrolled': len(already),
        'under_tier': under_tier,
        'unknown_emails': unknown,
    }
    if cohort is not None:
        body['cohort_enrollments'] = cohort_enrollments
    return JsonResponse(body, status=200)


@token_required
@csrf_exempt
@require_methods('DELETE')
@openapi_spec(
    tag="Course Enrollments",
    summary="Unenroll a user from a course (staff-only)",
    methods={
        "DELETE": {
            "summary": "Unenroll a user (soft-delete)",
            "description": (
                "Staff-only. Idempotent: returns 204 whether or not a "
                "row was changed. Soft-deletes the active enrollment by "
                "setting ``unenrolled_at``; the historical row is "
                "preserved so a follow-up POST can create a new active "
                "row. There is no GET / PATCH on this URL."
            ),
            "responses": {
                204: {"description": "Enrollment unenrolled (empty body)."},
                403: {
                    "description": "Non-staff bearer.",
                    "example": {
                        "error": "Enrollment delete is staff-only",
                        "code": "forbidden_other_user_plan",
                    },
                },
                404: {"description": "Course not found or unpublished."},
            },
        },
    },
)
def course_enrollment_detail(request, slug, email):
    """``DELETE /api/courses/<slug>/enrollments/<email>`` -- staff only.

    Idempotent: returns 204 whether or not a row was changed. Soft-
    deletes the active enrollment by setting ``unenrolled_at``; the
    historical row is preserved so a follow-up POST can create a new
    active row.
    """
    if not bearer_is_admin(request.user):
        return error_response(
            'Enrollment delete is staff-only',
            'forbidden_other_user_plan',
            status=403,
        )

    course = _get_published_course(slug)
    if course is None:
        return error_response(
            'Course not found', 'unknown_course', status=404,
        )

    target = User.objects.filter(email__iexact=email).first()
    if target is not None:
        unenroll(target, course, cause=UNENROLL_CAUSE_STAFF, actor=request.user)
    return JsonResponse({}, status=204)
