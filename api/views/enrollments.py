"""Sprint enrollment endpoints (issue #443).

Three endpoints:

- ``GET /api/sprints/<slug>/enrollments`` — list (scoped to bearer
  identity for non-staff).
- ``POST /api/sprints/<slug>/enrollments`` — staff-only bulk enroll
  with the four-bucket result shape (matches the Studio page exactly).
- ``DELETE /api/sprints/<slug>/enrollments/<email>`` — staff-only,
  idempotent unenroll that auto-privates an existing plan.

The contract mirrors the existing ``api/views/sprints.py`` style:
``token_required`` outermost so 401 fires before 405; canonical error
envelope via :func:`api.safety.error_response`.
"""

from django.contrib.auth import get_user_model
from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from api.openapi import openapi_spec
from api.safety import error_response
from api.utils import (
    delete_not_available_response,
    parse_json_body,
    require_methods,
)
from api.views._permissions import bearer_is_admin
from content.access import get_user_level
from plans.models import Sprint, SprintEnrollment

_SPRINT_ENROLLMENT_EXAMPLE = {
    "user_email": "alice@example.com",
    "enrolled_at": "2026-04-15T12:00:00+00:00",
    "enrolled_by": "staff@example.com",
}

User = get_user_model()

# Issue #864 (human decision, 2026-06-13): sprint-enrollment hard-delete is not
# available through the API. DELETE is accepted but returns 405 pointing the
# operator to Studio, matching the events guard pattern.
SPRINT_ENROLLMENT_DELETE_NOT_AVAILABLE_MESSAGE = (
    "Sprint enrollment deletion is not available through the API. "
    "Go to Studio to unenroll this user manually."
)


def _serialize_enrollment(enrollment):
    """JSON shape for a single enrollment row.

    ``enrolled_by`` is the email of the staff user who created the row,
    or ``None`` when the member self-joined.
    """
    return {
        'user_email': enrollment.user.email,
        'enrolled_at': (
            enrollment.enrolled_at.isoformat()
            if enrollment.enrolled_at else None
        ),
        'enrolled_by': (
            enrollment.enrolled_by.email
            if enrollment.enrolled_by_id else None
        ),
    }


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


@token_required
@csrf_exempt
@require_methods('GET', 'POST')
@openapi_spec(
    tag="Sprint Enrollments",
    summary="List or bulk-enroll sprint participants",
    methods={
        "GET": {
            "summary": "List sprint enrollments",
            "description": (
                "Staff sees every enrollment in the sprint; non-staff "
                "sees only their own row."
            ),
            "responses": {
                200: {
                    "description": "List of enrollments.",
                    "example": {
                        "enrollments": [_SPRINT_ENROLLMENT_EXAMPLE],
                    },
                },
                404: {
                    "description": "Sprint not found.",
                    "example": {
                        "error": "Sprint not found",
                        "code": "unknown_sprint",
                    },
                },
            },
        },
        "POST": {
            "summary": "Bulk enroll users (staff-only)",
            "description": (
                "Tier mismatches are flagged as ``under_tier`` "
                "(warning) but the enrollment is still created -- "
                "staff are explicitly choosing to enroll the user."
            ),
            "request_body": {
                "required": ["user_emails"],
                "properties": {
                    "user_emails": {
                        "type": "array",
                        "items": {"type": "string", "format": "email"},
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
                    },
                },
                403: {
                    "description": "Non-staff bearer.",
                    "example": {
                        "error": "Bulk enrollment is staff-only",
                        "code": "forbidden_other_user_plan",
                    },
                },
                404: {"description": "Sprint not found."},
                422: {"description": "Missing or invalid user_emails."},
            },
        },
    },
)
def sprint_enrollments_collection(request, slug):
    """``GET / POST /api/sprints/<slug>/enrollments``."""
    sprint = Sprint.objects.filter(slug=slug).first()
    if sprint is None:
        return error_response(
            'Sprint not found', 'unknown_sprint', status=404,
        )

    if request.method == 'GET':
        qs = SprintEnrollment.objects.filter(sprint=sprint).select_related(
            'user', 'enrolled_by',
        )
        if not bearer_is_admin(request.user):
            qs = qs.filter(user=request.user)
        return JsonResponse(
            {'enrollments': [_serialize_enrollment(e) for e in qs]},
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
            details={'field': 'body', 'expected': 'object'},
        )
    raw_emails = data.get('user_emails')
    if not isinstance(raw_emails, list):
        return error_response(
            'Missing required field: user_emails',
            'missing_field',
            status=422,
            details={'field': 'user_emails'},
        )

    emails = _normalize_emails(raw_emails)
    enrolled = []
    already = []
    under_tier = []
    unknown = []

    with transaction.atomic():
        users_by_email = {
            u.email.lower(): u
            for u in User.objects.filter(email__in=emails)
        }
        existing = set(
            SprintEnrollment.objects.filter(
                sprint=sprint, user__email__in=emails,
            ).values_list('user__email', flat=True)
        )
        existing_lower = {e.lower() for e in existing}

        for email in emails:
            user = users_by_email.get(email)
            if user is None:
                unknown.append(email)
                continue
            if email in existing_lower:
                already.append(email)
            else:
                SprintEnrollment.objects.create(
                    sprint=sprint, user=user, enrolled_by=request.user,
                )
                enrolled.append(email)
            if get_user_level(user) < sprint.min_tier_level:
                under_tier.append(email)

    return JsonResponse(
        {
            'enrolled': len(enrolled),
            'already_enrolled': len(already),
            'under_tier': under_tier,
            'unknown_emails': unknown,
        },
        status=200,
    )


@token_required
@csrf_exempt
@require_methods('DELETE')
@openapi_spec(
    tag="Sprint Enrollments",
    summary="DELETE is not available on this route",
    methods={
        "DELETE": {
            "summary": "DELETE is not available on this route",
            "description": (
                "Sprint enrollment deletion is not available through the "
                "API (issue #864); unenroll a user in Studio instead. "
                "DELETE returns a structured 405."
            ),
            "responses": {
                405: {
                    "description": "Enrollment deletion is not available.",
                    "example": {
                        "error": SPRINT_ENROLLMENT_DELETE_NOT_AVAILABLE_MESSAGE,
                        "code": "sprint_enrollment_delete_not_available",
                    },
                },
            },
        },
    },
)
def sprint_enrollment_detail(request, slug, email):
    """``DELETE /api/sprints/<slug>/enrollments/<email>``.

    Deletion is intentionally unavailable (issue #864): returns 405 with a
    Studio pointer. Unenroll a user in Studio instead.
    """
    return delete_not_available_response(
        SPRINT_ENROLLMENT_DELETE_NOT_AVAILABLE_MESSAGE,
        "sprint_enrollment_delete_not_available",
    )
