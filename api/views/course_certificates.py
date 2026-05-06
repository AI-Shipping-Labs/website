"""Manual course-certificate awarding endpoints (issue #445).

Three staff-only endpoints for backfill / manual cert awarding:

- ``GET /api/courses/<slug>/certificates`` — list certs, ordered by
  ``issued_at`` descending.
- ``POST /api/courses/<slug>/certificates`` — create-or-update on
  ``(user, course)``. Existing rows update only ``pdf_url`` and
  ``submission_id``; ``issued_at`` is immutable (it's the historical
  issue date and ``auto_now_add``).
- ``DELETE /api/courses/<slug>/certificates/<email>`` — hard-delete.
  Idempotent: 204 whether or not a row existed.

The PDF URL field is validated to ``http`` / ``https`` schemes only —
the operator's real URLs are http (not https), but ``file://``,
``javascript:``, ``ftp://`` and friends are rejected as 422
``invalid_url``. ``submission_id`` pointing to a submission for a
different course is rejected as 422 ``invalid_submission`` (defence
in depth — we don't want a cert that references a foreign-course
submission).
"""

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from api.safety import error_response
from api.utils import parse_json_body, require_methods
from api.views._permissions import bearer_is_admin
from content.models import Course
from content.models.peer_review import CourseCertificate, ProjectSubmission

User = get_user_model()

_url_validator = URLValidator(schemes=['http', 'https'])


def _serialize_certificate(cert):
    return {
        'id': str(cert.id),
        'user_email': cert.user.email,
        'pdf_url': cert.pdf_url,
        'submission_id': cert.submission_id,
        'issued_at': cert.issued_at.isoformat() if cert.issued_at else None,
    }


def _get_published_course(slug):
    return Course.objects.filter(slug=slug, status='published').first()


def _validate_pdf_url(pdf_url):
    """Return None on valid (or empty) URL; otherwise an error_response."""
    if pdf_url == '':
        return None
    try:
        _url_validator(pdf_url)
    except ValidationError:
        return error_response(
            'pdf_url must be an http or https URL',
            'invalid_url',
            status=422,
            details={'field': 'pdf_url'},
        )
    return None


@token_required
@csrf_exempt
@require_methods('GET', 'POST')
def course_certificates_collection(request, slug):
    """``GET / POST /api/courses/<slug>/certificates`` -- staff only."""
    if not bearer_is_admin(request.user):
        return error_response(
            'Certificate API is staff-only',
            'forbidden_other_user_plan',
            status=403,
        )

    course = _get_published_course(slug)
    if course is None:
        return error_response(
            'Course not found', 'unknown_course', status=404,
        )

    if request.method == 'GET':
        qs = (
            CourseCertificate.objects
            .filter(course=course)
            .select_related('user')
            .order_by('-issued_at')
        )
        return JsonResponse(
            {'certificates': [_serialize_certificate(c) for c in qs]},
            status=200,
        )

    # POST
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

    raw_email = data.get('user_email')
    if not isinstance(raw_email, str) or not raw_email.strip():
        return error_response(
            'Missing required field: user_email',
            'missing_field',
            status=422,
            details={'field': 'user_email'},
        )
    email = raw_email.strip().lower()

    pdf_url = data.get('pdf_url', '')
    if pdf_url is None:
        pdf_url = ''
    if not isinstance(pdf_url, str):
        return error_response(
            'pdf_url must be a string',
            'invalid_type',
            status=422,
            details={'field': 'pdf_url'},
        )
    pdf_url = pdf_url.strip()
    url_err = _validate_pdf_url(pdf_url)
    if url_err is not None:
        return url_err

    # ``submission_id`` is allowed to be missing OR explicitly null.
    submission_id = data.get('submission_id', None)
    submission = None
    if submission_id is not None:
        if not isinstance(submission_id, int) or isinstance(
            submission_id, bool,
        ):
            return error_response(
                'submission_id must be an integer or null',
                'invalid_type',
                status=422,
                details={'field': 'submission_id'},
            )
        submission = ProjectSubmission.objects.filter(
            pk=submission_id,
        ).first()
        if submission is None or submission.course_id != course.pk:
            return error_response(
                'submission_id does not belong to this course',
                'invalid_submission',
                status=422,
                details={'field': 'submission_id'},
            )

    user = User.objects.filter(email__iexact=email).first()
    if user is None:
        return error_response(
            'No user matches that email',
            'unknown_user',
            status=422,
            details={'field': 'user_email'},
        )

    with transaction.atomic():
        existing = CourseCertificate.objects.filter(
            user=user, course=course,
        ).first()
        if existing is None:
            cert = CourseCertificate.objects.create(
                user=user,
                course=course,
                pdf_url=pdf_url,
                submission=submission,
            )
            created = True
        else:
            # NEVER touch issued_at; NEVER re-bind user. Update only the
            # mutable fields.
            existing.pdf_url = pdf_url
            existing.submission = submission
            existing.save(update_fields=['pdf_url', 'submission'])
            cert = existing
            created = False

    payload = _serialize_certificate(cert)
    payload['created'] = created
    return JsonResponse(payload, status=200)


@token_required
@csrf_exempt
@require_methods('DELETE')
def course_certificate_detail(request, slug, email):
    """``DELETE /api/courses/<slug>/certificates/<email>`` -- staff only.

    Idempotent: returns 204 whether or not a row was deleted.
    """
    if not bearer_is_admin(request.user):
        return error_response(
            'Certificate delete is staff-only',
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
        CourseCertificate.objects.filter(
            user=target, course=course,
        ).delete()
    return JsonResponse({}, status=204)
