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
from api.safety import error_response
from api.utils import parse_json_body, require_methods
from api.views._permissions import bearer_is_admin
from content.access import get_user_level
from plans.models import Plan, Sprint, SprintEnrollment

User = get_user_model()


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
def sprint_enrollment_detail(request, slug, email):
    """``DELETE /api/sprints/<slug>/enrollments/<email>`` -- staff only.

    Idempotent: returns 204 whether or not a row was deleted. Auto-
    privates the user's plan if one exists, mirroring the member-leave
    flow.
    """
    if not bearer_is_admin(request.user):
        return error_response(
            'Enrollment delete is staff-only',
            'forbidden_other_user_plan',
            status=403,
        )

    sprint = Sprint.objects.filter(slug=slug).first()
    if sprint is None:
        return error_response(
            'Sprint not found', 'unknown_sprint', status=404,
        )

    target = User.objects.filter(email__iexact=email).first()

    with transaction.atomic():
        if target is not None:
            SprintEnrollment.objects.filter(
                sprint=sprint, user=target,
            ).delete()
            plan = Plan.objects.filter(
                sprint=sprint, member=target,
            ).first()
            if plan is not None and plan.visibility != 'private':
                plan.visibility = 'private'
                plan.save(update_fields=['visibility', 'updated_at'])

    return JsonResponse({}, status=204)
