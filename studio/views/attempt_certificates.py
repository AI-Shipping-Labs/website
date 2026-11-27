"""Staff Studio views for attempt-scoped graduate certificate generation and preview."""

import logging

from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST

from content.models import Course, CourseProject
from content.services.attempt_certificates import (
    generate_attempt_certificates,
    preview_sample_certificate_pdf,
)
from integrations.services.banner_generator import BannerGeneratorError
from studio.decorators import staff_required

logger = logging.getLogger(__name__)


def _generation_summary_message(project, result):
    parts = [
        f'{result.created_count} created',
        f'{result.retried_count} retried',
        f'{result.skipped_issued_count} already issued',
    ]
    if result.skipped_revoked_count:
        parts.append(f'{result.skipped_revoked_count} revoked skipped')
    if result.skipped_in_progress_count:
        parts.append(f'{result.skipped_in_progress_count} in progress')
    if result.failed_count:
        parts.append(f'{result.failed_count} failed')
    joined = ', '.join(parts)
    return f'{project.title} certificates: {joined}.'


@staff_required
@require_POST
def peer_review_generate_certificates(request, course_id, project_id):
    """Generate missing graduate certificates for one completed attempt."""
    course = get_object_or_404(Course, pk=course_id)
    project = get_object_or_404(CourseProject, pk=project_id, course=course)

    result = generate_attempt_certificates(course, project)
    if result.error:
        messages.error(request, result.error)
        return redirect('studio_peer_review_management', course_id=course.pk)

    summary = _generation_summary_message(project, result)
    logger.info(
        'studio.attempt_certificates_generate actor=%s course_id=%s project_id=%s '
        'created=%s retried=%s skipped_issued=%s skipped_revoked=%s failed=%s',
        request.user.pk,
        course.pk,
        project.pk,
        result.created_count,
        result.retried_count,
        result.skipped_issued_count,
        result.skipped_revoked_count,
        result.failed_count,
    )
    if result.failed_count:
        messages.warning(request, summary)
    elif result.created_count or result.retried_count:
        messages.success(request, summary)
    else:
        messages.info(request, summary)
    return redirect('studio_peer_review_management', course_id=course.pk)


def _preview_recipient_name(user):
    full_name = str(user.get_full_name() or '').strip()
    if full_name:
        return full_name
    return 'Sample Graduate'


@staff_required
@require_POST
def peer_review_preview_certificate(request, course_id, project_id):
    """Render a private certificate PDF preview without issuing or publishing."""
    course = get_object_or_404(Course, pk=course_id)
    project = get_object_or_404(CourseProject, pk=project_id, course=course)
    if not project.is_completed():
        messages.error(
            request,
            'Certificate preview is only available for a completed dated attempt.',
        )
        return redirect('studio_peer_review_management', course_id=course.pk)

    recipient_name = _preview_recipient_name(request.user)
    try:
        artifact = preview_sample_certificate_pdf(
            course,
            project,
            recipient_name=recipient_name,
        )
    except BannerGeneratorError as exc:
        messages.error(request, f'Could not render certificate preview: {exc}')
        return redirect('studio_peer_review_management', course_id=course.pk)

    response = HttpResponse(artifact.content, content_type=artifact.content_type)
    response['Content-Disposition'] = f'inline; filename="{artifact.filename}"'
    return response
