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
    """Bundle a submission with its review counts for template rendering."""
    reviews = list(sub.reviews.select_related('reviewer').all())
    total_reviews = len(reviews)
    completed_reviews = sum(1 for r in reviews if r.is_complete)
    return {
        'submission': sub,
        'reviews': reviews,
        'total_reviews': total_reviews,
        'completed_reviews': completed_reviews,
        'status_label': _friendly_status_label(sub.status),
    }


@staff_required
def peer_review_management(request, course_id):
    """Show submissions for a course, grouped by cohort.

    Submissions without a cohort go into a `Self-paced / no cohort` group so
    operators can see which work item belongs to which cohort timeline.
    """
    course = get_object_or_404(Course, pk=course_id)

    status_filter = request.GET.get('status', '')

    submissions_qs = ProjectSubmission.objects.filter(
        course=course,
    ).select_related('user', 'cohort').order_by('-submitted_at')

    if status_filter:
        submissions_qs = submissions_qs.filter(status=status_filter)

    # Build per-cohort buckets in a deterministic order.  We always include
    # every cohort defined for the course so operators can see active cohorts
    # that have not yet received submissions.
    cohorts = list(Cohort.objects.filter(course=course).order_by('-start_date'))

    # Map cohort_id -> {cohort, submissions, ...}; key None is the self-paced bucket.
    groups_by_cohort = {}
    for cohort in cohorts:
        groups_by_cohort[cohort.pk] = {
            'cohort': cohort,
            'is_self_paced': False,
            'name': cohort.name,
            'start_date': cohort.start_date,
            'end_date': cohort.end_date,
            'is_active': cohort.is_active,
            'enrollment_count': cohort.enrollment_count,
            'items': [],
        }

    self_paced_group = {
        'cohort': None,
        'is_self_paced': True,
        'name': 'Self-paced / no cohort',
        'start_date': None,
        'end_date': None,
        'is_active': True,
        'enrollment_count': None,
        'items': [],
    }

    for sub in submissions_qs:
        item = _build_submission_item(sub)
        if sub.cohort_id and sub.cohort_id in groups_by_cohort:
            groups_by_cohort[sub.cohort_id]['items'].append(item)
        else:
            self_paced_group['items'].append(item)

    cohort_groups = list(groups_by_cohort.values())
    # Append the self-paced bucket if it has submissions, OR if there are no
    # cohorts at all (so the page still renders a single grouped section).
    if self_paced_group['items'] or not cohorts:
        cohort_groups.append(self_paced_group)

    # Status filter chip data with friendly labels and counts.  Counts ignore
    # the current filter so operators always see the full distribution.
    base_qs = ProjectSubmission.objects.filter(course=course)
    status_counts = {
        code: base_qs.filter(status=code).count()
        for code, _label in SUBMISSION_STATUS_CHOICES
    }
    status_filters = [
        {
            'value': code,
            'label': FRIENDLY_STATUS_LABELS.get(code, label),
            'count': status_counts[code],
            'active': status_filter == code,
        }
        for code, label in SUBMISSION_STATUS_CHOICES
    ]
    total_submissions = base_qs.count()
    waiting_count = status_counts.get('submitted', 0)

    context = {
        'course': course,
        'cohort_groups': cohort_groups,
        'has_any_cohorts': bool(cohorts),
        'status_filter': status_filter,
        'status_filter_label': _friendly_status_label(status_filter) if status_filter else '',
        'status_filters': status_filters,
        'total_submissions': total_submissions,
        'waiting_count': waiting_count,
    }
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
        course=course, status='in_review',
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
