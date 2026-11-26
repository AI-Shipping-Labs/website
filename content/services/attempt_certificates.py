"""AISL adapter over ``community_base.coursework.attempt_certificates`` (intake 32366cde)."""

from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager

from community_base.coursework.attempt_certificates import (
    AttemptCertificateGenerationResult,
    AttemptCertificatePreview,
    CompletionPolicyDecision,
    ExistingCertificate,
    PrivatePreviewArtifact,
    evaluate_attempt_certificates,
    generate_missing_attempt_certificates,
    render_private_certificate_preview,
)
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.utils import timezone

from content.models import (
    CourseCertificate,
    PeerReview,
    ProjectSubmission,
    Unit,
    UserCourseProgress,
)
from content.services.peer_review_service import certificate_required_units
from integrations.services.banner_generator.certificates import (
    render_certificate_pdf_to_s3,
    render_certificate_preview_pdf,
)

LOCK_TTL_SECONDS = 300


def _to_existing_certificate(cert):
    return ExistingCertificate(
        certificate_id=str(cert.id),
        user_id=cert.user_id,
        attachment_url=cert.pdf_url,
        is_revoked=cert.is_revoked,
        issued_at=cert.issued_at,
        submission_id=cert.submission_id,
    )


def _course_completion_policy(course, project):
    required_units = certificate_required_units(
        Unit.objects.filter(module__course=course),
        project,
    )
    required_ids = list(required_units.values_list('pk', flat=True))
    total_required = len(required_ids)
    if total_required == 0:
        return '', None

    def _check(submission):
        completed = UserCourseProgress.objects.filter(
            user_id=submission.user_id,
            unit_id__in=required_ids,
            completed_at__isnull=False,
        ).count()
        if completed >= total_required:
            return CompletionPolicyDecision(allowed=True)
        return CompletionPolicyDecision(
            allowed=False,
            reason=f'{completed} of {total_required} required units completed.',
        )

    return 'All required course units completed', _check


def _reviews_for_project(project, submissions):
    assigned_by_user = defaultdict(list)
    assigned_qs = PeerReview.objects.filter(
        submission__course_project=project,
    ).select_related('submission').order_by('pk')
    for review in assigned_qs:
        assigned_by_user[review.reviewer_id].append(review)

    received_by_submission = defaultdict(list)
    if submissions:
        received_qs = PeerReview.objects.filter(
            submission__in=submissions,
        ).order_by('pk')
        for review in received_qs:
            received_by_submission[review.submission_id].append(review)

    return assigned_by_user, received_by_submission


def _existing_certificates_by_user(course, submissions):
    user_ids = [sub.user_id for sub in submissions]
    if not user_ids:
        return {}
    certs = CourseCertificate.objects.filter(
        course=course,
        user_id__in=user_ids,
    )
    result = {}
    for cert in certs:
        result[cert.user_id] = _to_existing_certificate(cert)
    return result


def preview_attempt_certificates(course, project) -> AttemptCertificatePreview:
    """Preview eligible, missing, and already-issued graduate counts for one attempt."""
    submissions = list(
        ProjectSubmission.objects.filter(
            course=course,
            course_project=project,
        ).select_related('user').order_by('pk')
    )
    assigned_by_user, received_by_submission = _reviews_for_project(
        project, submissions,
    )
    existing_by_user = _existing_certificates_by_user(course, submissions)
    policy_name, completion_policy = _course_completion_policy(course, project)
    passing_score = course.project_passing_score or 0
    criteria = project.evaluation_criteria or []
    return evaluate_attempt_certificates(
        project,
        submissions=submissions,
        assigned_by_user=assigned_by_user,
        received_by_submission=received_by_submission,
        criteria=criteria,
        passing_score=passing_score,
        existing_by_user=existing_by_user,
        completion_policy=completion_policy,
        policy_name=policy_name,
    )


class AISLAttemptCertificateAdapter:
    """Persists ``CourseCertificate`` rows and renders PDFs via Banner Generator."""

    def __init__(self, course, project, *, render_to_s3_fn=None):
        self.course = course
        self.project = project
        if render_to_s3_fn is None:
            self._render_to_s3 = render_certificate_pdf_to_s3
        else:
            self._render_to_s3 = render_to_s3_fn

    @contextmanager
    def acquire_candidate_lock(self, submission):
        key = f'attempt-cert-lock:{self.course.pk}:{submission.user_id}'
        acquired = bool(cache.add(key, '1', timeout=LOCK_TTL_SECONDS))
        try:
            yield acquired
        finally:
            if acquired:
                cache.delete(key)

    def reserve_credential(self, submission):
        if submission.course_id != self.course.pk:
            raise ValueError('Submission does not belong to this course.')
        with transaction.atomic():
            existing = CourseCertificate.objects.select_for_update().filter(
                user_id=submission.user_id,
                course=self.course,
            ).first()
            if existing is not None:
                return _to_existing_certificate(existing), False
            try:
                cert = CourseCertificate.objects.create(
                    user_id=submission.user_id,
                    course=self.course,
                    submission=submission,
                )
            except IntegrityError:
                cert = CourseCertificate.objects.get(
                    user_id=submission.user_id,
                    course=self.course,
                )
                return _to_existing_certificate(cert), False
            return _to_existing_certificate(cert), True

    def render_attachment(self, submission, credential):
        cert = CourseCertificate.objects.select_related('user').get(
            pk=credential.certificate_id,
            user_id=submission.user_id,
            course=self.course,
        )
        return self._render_to_s3(cert, course=self.course)

    def persist_attachment(self, submission, credential, attachment_url):
        with transaction.atomic():
            updated = CourseCertificate.objects.filter(
                pk=credential.certificate_id,
                user_id=submission.user_id,
                course=self.course,
                pdf_url='',
                revoked_at__isnull=True,
            ).update(pdf_url=attachment_url)
            if not updated:
                return False
            ProjectSubmission.objects.filter(
                pk=submission.pk,
                user_id=submission.user_id,
                course=self.course,
            ).update(
                status='certified',
                certificate_issued_at=timezone.now(),
            )
            return True


def generate_attempt_certificates(
    course,
    project,
    *,
    render_to_s3_fn=None,
) -> AttemptCertificateGenerationResult:
    """Generate missing certificates for one completed attempt; never sends email."""
    preview = preview_attempt_certificates(course, project)
    adapter = AISLAttemptCertificateAdapter(
        course,
        project,
        render_to_s3_fn=render_to_s3_fn,
    )
    return generate_missing_attempt_certificates(preview, adapter)


def preview_sample_certificate_pdf(
    course,
    project,
    *,
    recipient_name,
    preview_fn=None,
) -> PrivatePreviewArtifact:
    """Render a private preview PDF without issuing credentials or publishing objects."""
    if preview_fn is None:
        renderer = render_certificate_preview_pdf
    else:
        renderer = preview_fn

    def _call(context):
        return renderer(
            recipient_name=context['recipient_name'],
            course=context['course'],
        )

    return render_private_certificate_preview(
        _call,
        {'recipient_name': recipient_name, 'course': course, 'project': project},
    )
