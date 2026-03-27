"""Studio views for managing peer reviews."""

import logging

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from content.models import Course, ProjectSubmission
from content.services.peer_review_service import PeerReviewService
from studio.decorators import staff_required

logger = logging.getLogger(__name__)


@staff_required
def peer_review_management(request, course_id):
    """Show all submissions for a course with status badges and review info."""
    course = get_object_or_404(Course, pk=course_id)

    status_filter = request.GET.get('status', '')
    submissions = ProjectSubmission.objects.filter(
        course=course,
    ).select_related('user', 'cohort').order_by('-submitted_at')

    if status_filter:
        submissions = submissions.filter(status=status_filter)

    # Annotate with review counts
    submission_data = []
    for sub in submissions:
        reviews = sub.reviews.select_related('reviewer').all()
        total_reviews = reviews.count()
        completed_reviews = reviews.filter(is_complete=True).count()

        submission_data.append({
            'submission': sub,
            'reviews': reviews,
            'total_reviews': total_reviews,
            'completed_reviews': completed_reviews,
        })

    # Count waiting submissions
    waiting_count = ProjectSubmission.objects.filter(
        course=course, status='submitted',
    ).count()

    context = {
        'course': course,
        'submission_data': submission_data,
        'status_filter': status_filter,
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
            f'Formed batch: {batched} submissions batched, {reviews} reviews assigned.',
        )
    else:
        messages.info(request, 'No submissions ready for batching.')

    return redirect('studio_peer_review_management', course_id=course.pk)


@staff_required
@require_POST
def peer_review_issue_certificates(request, course_id):
    """Manually issue certificates for eligible students."""
    course = get_object_or_404(Course, pk=course_id)

    count = PeerReviewService.issue_certificates_for_course(course)

    if count > 0:
        messages.success(request, f'Issued {count} certificate(s).')
    else:
        messages.info(request, 'No eligible students for certificates.')

    return redirect('studio_peer_review_management', course_id=course.pk)


@staff_required
@require_POST
def peer_review_extend_deadline(request, course_id):
    """Extend review deadline for all in-review submissions."""
    course = get_object_or_404(Course, pk=course_id)
    days = int(request.POST.get('days', 7))

    from datetime import timedelta


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
