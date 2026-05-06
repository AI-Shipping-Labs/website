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
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from api.safety import error_response
from api.utils import parse_json_body, require_methods
from api.views._permissions import bearer_is_admin
from content.access import can_access
from content.models import Course
from content.models.enrollment import SOURCE_ADMIN, Enrollment
from content.services.enrollment import ensure_enrollment, unenroll

User = get_user_model()


def _serialize_enrollment(enrollment):
    """JSON shape for a single course-enrollment row."""
    return {
        'user_email': enrollment.user.email,
        'enrolled_at': (
            enrollment.enrolled_at.isoformat()
            if enrollment.enrolled_at else None
        ),
        'unenrolled_at': (
            enrollment.unenrolled_at.isoformat()
            if enrollment.unenrolled_at else None
        ),
        'source': enrollment.source,
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


def _get_published_course(slug):
    """Return the published Course or None."""
    return Course.objects.filter(slug=slug, status='published').first()


@token_required
@csrf_exempt
@require_methods('GET', 'POST')
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

    emails = _normalize_emails(combined)
    enrolled = []
    already = []
    under_tier = []
    unknown = []

    with transaction.atomic():
        users_by_email = {
            u.email.lower(): u
            for u in User.objects.filter(email__in=emails)
        }

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
        unenroll(target, course)
    return JsonResponse({}, status=204)
