"""Studio views for managing peer reviews."""

import logging
from datetime import timedelta

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from content.models import Cohort, Course, ProjectSubmission
from content.models.peer_review import SUBMISSION_STATUS_CHOICES
from content.services.peer_review_service import PeerReviewService
from studio.decorators import staff_required

logger = logging.getLogger(__name__)


# Friendly labels for status chips/filters in the management UI.
FRIENDLY_STATUS_LABELS = {
    'submitted': 'Awaiting reviewers',
    'in_review': 'Being reviewed',
    'review_complete': 'Reviews complete',
    'certified': 'Certificate issued',
}


def _friendly_status_label(status):
    """Return the operator-facing label for a submission status code."""
    return FRIENDLY_STATUS_LABELS.get(status, status.replace('_', ' ').capitalize())


def _build_submission_item(sub):
    """Bundle a submission with its review counts for template rendering.

    Also surfaces the certificate (if any) so the revoke / un-revoke
    control can be rendered inline (issue #949). A submission has at most
    one certificate via the ``submission`` FK on :class:`CourseCertificate`.
    """
    reviews = list(sub.reviews.select_related('reviewer').all())
    total_reviews = len(reviews)
    completed_reviews = sum(1 for r in reviews if r.is_complete)
    # The submission -> certificate FK is nullable and back-creates the
    # ``certificates`` related set; a certified submission carries one row.
    certificate = sub.certificates.first()
    return {
        'submission': sub,
        'reviews': reviews,
        'total_reviews': total_reviews,
        'completed_reviews': completed_reviews,
        'status_label': _friendly_status_label(sub.status),
        'certificate': certificate,
    }


def _cohort_group(cohort):
    return {
        'cohort': cohort,
        'is_self_paced': cohort.mode == 'self_paced',
        'is_legacy_unscoped': False,
        'name': cohort.name,
        'start_date': cohort.start_date,
        'end_date': cohort.end_date,
        'is_active': cohort.is_active,
        'enrollment_count': cohort.enrollment_count,
        'items': [],
    }


def _legacy_group():
    return {
        'cohort': None,
        'is_self_paced': False,
        'is_legacy_unscoped': True,
        'name': 'Legacy / no cohort',
        'start_date': None,
        'end_date': None,
        'is_active': True,
        'enrollment_count': None,
        'items': [],
    }


def _submission_groups(course, submissions):
    cohorts = list(Cohort.objects.filter(course=course).order_by('-start_date'))
    groups = {cohort.pk: _cohort_group(cohort) for cohort in cohorts}
    legacy = _legacy_group()
    for submission in submissions:
        item = _build_submission_item(submission)
        group = groups.get(submission.cohort_id, legacy)
        group['items'].append(item)
    cohort_groups = list(groups.values())
    if legacy['items'] or not cohorts:
        cohort_groups.append(legacy)
    return cohorts, cohort_groups


def _status_context(course, status_filter):
    base_qs = ProjectSubmission.objects.filter(course=course)
    counts = {
        code: base_qs.filter(status=code).count()
        for code, _label in SUBMISSION_STATUS_CHOICES
    }
    filters = [
        {
            'value': code,
            'label': FRIENDLY_STATUS_LABELS.get(code, label),
            'count': counts[code],
            'active': status_filter == code,
        }
        for code, label in SUBMISSION_STATUS_CHOICES
    ]
    return {
        'status_filters': filters,
        'total_submissions': base_qs.count(),
        'waiting_count': counts.get('submitted', 0),
    }


@staff_required
def peer_review_management(request, course_id):
    """Show submissions for a course, grouped by cohort.

    Submissions without a cohort go into a distinct legacy group so
    operators can see which work item belongs to which cohort timeline.
    """
    course = get_object_or_404(Course, pk=course_id)

    status_filter = request.GET.get('status', '')

    submissions_qs = ProjectSubmission.objects.filter(
        course=course,
    ).select_related('user', 'cohort', 'course_project').order_by('-submitted_at')

    if status_filter:
        submissions_qs = submissions_qs.filter(status=status_filter)

    cohorts, cohort_groups = _submission_groups(course, submissions_qs)

    context = {
        'course': course,
        'course_projects': list(course.course_projects.select_related('cohort')),
        'cohort_groups': cohort_groups,
        'has_any_cohorts': bool(cohorts),
        'status_filter': status_filter,
        'status_filter_label': _friendly_status_label(status_filter) if status_filter else '',
    }
    context.update(_status_context(course, status_filter))
    return render(request, 'studio/courses/peer_reviews.html', context)


@staff_required
@require_POST
def peer_review_form_batch(request, course_id):
    """Manually trigger batch formation for waiting submissions."""
    course = get_object_or_404(Course, pk=course_id)

    if not course.peer_review_enabled:
        messages.error(request, 'Peer review is not enabled for this course.')
        return redirect('studio_peer_review_management', course_id=course.pk)

    result = PeerReviewService.form_batches_for_course(course)
    batched = result['batched']
    reviews = result['reviews_assigned']

    if batched > 0:
        messages.success(
            request,
            f'Created review assignments: {batched} submission(s) batched, '
            f'{reviews} review(s) assigned.',
        )
    else:
        messages.info(request, 'No submissions ready for review assignments.')

    return redirect('studio_peer_review_management', course_id=course.pk)


@staff_required
@require_POST
def peer_review_issue_certificates(request, course_id):
    """Manually issue certificates for eligible students."""
    course = get_object_or_404(Course, pk=course_id)

    count = PeerReviewService.issue_certificates_for_course(course)

    if count > 0:
        messages.success(request, f'Issued {count} certificate(s) for eligible completions.')
    else:
        messages.info(request, 'No eligible completions for certificate issuance.')

    return redirect('studio_peer_review_management', course_id=course.pk)


@staff_required
@require_POST
def peer_review_extend_deadline(request, course_id):
    """Extend review deadline for all in-review submissions."""
    course = get_object_or_404(Course, pk=course_id)
    days = int(request.POST.get('days', 7))

    updated = 0
    submissions = ProjectSubmission.objects.filter(
        course=course, course_project__isnull=True, status='in_review',
    )
    for sub in submissions:
        if sub.review_deadline:
            sub.review_deadline += timedelta(days=days)
            sub.save(update_fields=['review_deadline'])
            updated += 1

    if updated:
        messages.success(request, f'Extended deadline by {days} days for {updated} submission(s).')
    else:
        messages.info(request, 'No in-review submissions to extend.')

    return redirect('studio_peer_review_management', course_id=course.pk)
