"""Course access grant and revoke endpoints (staff-only).

- ``POST /api/courses/<slug>/access`` grants ``CourseAccess(granted)``
  to a list of emails or to every current member of a dated cohort.
- ``DELETE /api/courses/<slug>/access/<email>`` removes a ``granted``
  row; purchased access is never touched.

Both run through ``content.services.course_access`` -- the same service
as the Studio course access page. Granting sends no email.
"""

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from accounts.services.email_resolution import (
    resolve_user_by_email,
    resolve_users_by_emails,
)
from api.openapi import openapi_spec
from api.safety import error_response
from api.utils import parse_json_body, require_methods
from content.models import CohortEnrollment, Course
from content.services.course_access import (
    GRANT_ALREADY_HAS_ACCESS,
    GRANT_GRANTED,
    GRANT_WOULD_GRANT,
    REVOKE_PURCHASED,
    grant_course_access,
    revoke_granted_course_access,
)
from content.services.course_cohorts import get_course_cohort_by_key

BATCH_CAP = 1000

STATUS_USER_NOT_FOUND = 'user_not_found'
STATUS_MALFORMED = 'malformed'
_COUNTED_STATUSES = (
    GRANT_ALREADY_HAS_ACCESS, STATUS_USER_NOT_FOUND, STATUS_MALFORMED,
)


def _normalized_email_or_none(value):
    """Return the stripped, lowercased email, or None when malformed."""
    if not isinstance(value, str):
        return None
    cleaned = value.strip().lower()
    if not cleaned:
        return None
    try:
        validate_email(cleaned)
    except ValidationError:
        return None
    return cleaned


def _email_rows(raw_emails):
    """Resolve raw emails to ``[(email, user_or_None, malformed)]``.

    Order is preserved and duplicates (after normalization) collapse to
    the first occurrence. Two lookup queries for the whole batch.
    """
    rows = []
    seen = set()
    for raw in raw_emails:
        email = _normalized_email_or_none(raw)
        if email is None:
            rows.append([raw, None, True])
            continue
        if email in seen:
            continue
        seen.add(email)
        rows.append([email, None, False])
    users = resolve_users_by_emails(
        [email for email, _, malformed in rows if not malformed],
    )
    return [
        (email, None if malformed else users.get(email), malformed)
        for email, _, malformed in rows
    ]


def _cohort_rows(cohort):
    """Every current member of ``cohort`` as ``(email, user, False)``."""
    members = (
        CohortEnrollment.objects.filter(cohort=cohort)
        .select_related('user')
        .order_by('user__email')
    )
    return [(m.user.email, m.user, False) for m in members]


def _invalid_body(message, field, expected):
    return error_response(
        message, 'invalid_type', status=422,
        details={'field': field, 'expected': expected},
    )


@token_required
@csrf_exempt
@require_methods('POST')
@openapi_spec(
    tag="Course Access",
    summary="Grant individual course access (staff-only)",
    methods={
        "POST": {
            "summary": "Grant granted-type course access",
            "description": (
                "Creates ``CourseAccess(access_type=\"granted\")`` for each "
                "user, recording the API token's owner as ``granted_by`` "
                "(the same service as the Studio grant). Pass either "
                "``emails`` (primary email first, then alias) or "
                "``cohort`` (a dated cohort's external key, e.g. ``1``) "
                "to grant every current member of that cohort. Idempotent: "
                "a user who already has granted or purchased access is "
                "reported as ``already_has_access``. Per-row ``status``: "
                "``granted``, ``already_has_access``, ``user_not_found``, "
                "or ``malformed``; ``dry_run: true`` reports "
                "``would_grant`` instead of ``granted`` and writes "
                f"nothing. Sends no email. Batch cap {BATCH_CAP} emails."
            ),
            "request_body": {
                "properties": {
                    "emails": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "cohort": {
                        "type": "string",
                        "description": (
                            "Cohort external key under this course "
                            "(case-insensitive). Mutually exclusive with "
                            "``emails``."
                        ),
                    },
                    "dry_run": {"type": "boolean"},
                },
                "example": {
                    "emails": ["alice@example.com", "bob@example.com"],
                    "dry_run": True,
                },
            },
            "responses": {
                200: {
                    "description": "Per-row grant results plus counts.",
                    "example": {
                        "course": "ai-buildcamp",
                        "cohort": None,
                        "dry_run": False,
                        "granted": 1,
                        "already_has_access": 1,
                        "user_not_found": 1,
                        "malformed": 0,
                        "results": [
                            {
                                "email": "alice@example.com",
                                "status": "granted",
                                "access_type": "granted",
                            },
                            {
                                "email": "bob@example.com",
                                "status": "already_has_access",
                                "access_type": "purchased",
                            },
                            {
                                "email": "ghost@example.com",
                                "status": "user_not_found",
                            },
                        ],
                    },
                },
                400: {
                    "description": (
                        "Unknown cohort or over-cap batch. Codes: "
                        "``unknown_cohort``, ``batch_too_large``."
                    ),
                    "example": {
                        "error": "Unknown cohort '9' for course 'ai-buildcamp'",
                        "code": "unknown_cohort",
                    },
                },
                404: {
                    "description": "Course not found.",
                    "example": {
                        "error": "Course not found",
                        "code": "unknown_course",
                    },
                },
                422: {
                    "description": (
                        "Body is not an object, both or neither of "
                        "``emails`` / ``cohort`` given, or a bad type."
                    ),
                },
            },
        },
    },
)
def course_access_collection(request, slug):
    """``POST /api/courses/<slug>/access``."""
    course = Course.objects.filter(slug=slug).first()
    if course is None:
        return error_response('Course not found', 'unknown_course', status=404)

    data, parse_error = parse_json_body(request)
    if parse_error is not None:
        return parse_error
    if not isinstance(data, dict):
        return _invalid_body('Body must be a JSON object', 'body', 'object')

    raw_emails = data.get('emails')
    raw_cohort = data.get('cohort')
    if (raw_emails is None) == (raw_cohort is None):
        return error_response(
            'Pass exactly one of emails or cohort',
            'missing_field',
            status=422,
            details={'field': 'emails_or_cohort'},
        )
    dry_run = data.get('dry_run') is True

    cohort = None
    if raw_cohort is not None:
        if isinstance(raw_cohort, bool) or not isinstance(raw_cohort, (str, int)):
            return _invalid_body('cohort must be a string', 'cohort', 'string')
        cohort_key = str(raw_cohort).strip()
        cohort = get_course_cohort_by_key(course, cohort_key)
        if cohort is None:
            return error_response(
                f'Unknown cohort {cohort_key!r} for course {course.slug!r}',
                'unknown_cohort',
                status=400,
                details={'field': 'cohort'},
            )
        rows = _cohort_rows(cohort)
    else:
        if not isinstance(raw_emails, list):
            return _invalid_body('emails must be a list', 'emails', 'array')
        if len(raw_emails) > BATCH_CAP:
            return error_response(
                f'Batch too large: {len(raw_emails)} exceeds cap of {BATCH_CAP}',
                'batch_too_large',
            )
        rows = _email_rows(raw_emails)

    with transaction.atomic():
        outcome = grant_course_access(
            course,
            [user for _, user, _ in rows if user is not None],
            actor=request.user,
            dry_run=dry_run,
        )

    results = []
    for email, user, malformed in rows:
        if malformed:
            results.append({'email': email, 'status': STATUS_MALFORMED})
        elif user is None:
            results.append({'email': email, 'status': STATUS_USER_NOT_FOUND})
        else:
            status, access_type = outcome[user.pk]
            results.append({
                'email': email, 'status': status, 'access_type': access_type,
            })

    counts = {
        status: sum(1 for r in results if r['status'] == status)
        for status in _COUNTED_STATUSES
    }
    granted = sum(
        1 for r in results
        if r['status'] in (GRANT_GRANTED, GRANT_WOULD_GRANT)
    )
    return JsonResponse({
        'course': course.slug,
        'cohort': cohort.external_key if cohort is not None else None,
        'dry_run': dry_run,
        'granted': granted,
        **counts,
        'results': results,
    }, status=200)


@token_required
@csrf_exempt
@require_methods('DELETE')
@openapi_spec(
    tag="Course Access",
    summary="Revoke granted course access (staff-only)",
    methods={
        "DELETE": {
            "summary": "Revoke a user's granted course access",
            "description": (
                "Deletes the user's ``CourseAccess`` row only when its "
                "type is ``granted`` (the same service as the Studio "
                "revoke). Purchased access is never revoked: the call "
                "returns ``409 purchased_access``. The email resolves by "
                "primary email, then alias. Idempotent: no access row "
                "returns ``200`` with ``status: no_access``."
            ),
            "responses": {
                200: {
                    "description": "Revoked, or there was nothing to revoke.",
                    "example": {
                        "email": "alice@example.com",
                        "status": "revoked",
                    },
                },
                404: {
                    "description": (
                        "Course or user not found. Codes: "
                        "``unknown_course``, ``user_not_found``."
                    ),
                },
                409: {
                    "description": "The user's access is purchased.",
                    "example": {
                        "error": "Purchased access cannot be revoked",
                        "code": "purchased_access",
                    },
                },
            },
        },
    },
)
def course_access_detail(request, slug, email):
    """``DELETE /api/courses/<slug>/access/<email>``."""
    course = Course.objects.filter(slug=slug).first()
    if course is None:
        return error_response('Course not found', 'unknown_course', status=404)
    user = resolve_user_by_email(email)
    if user is None:
        return error_response('User not found', 'user_not_found', status=404)

    status = revoke_granted_course_access(course, user, actor=request.user)
    if status == REVOKE_PURCHASED:
        return error_response(
            'Purchased access cannot be revoked',
            'purchased_access',
            status=409,
        )
    return JsonResponse({'email': user.email, 'status': status}, status=200)
