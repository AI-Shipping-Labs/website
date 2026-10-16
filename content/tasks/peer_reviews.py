"""Scheduled rolling review group formation for self-paced attempts."""

from content.models import CourseProject
from content.services.peer_review_service import PeerReviewService


def form_self_paced_review_batches():
    """Form ready groups for every active self-paced project pool."""
    summary = {'batched': 0, 'reviews_assigned': 0}
    projects = CourseProject.objects.select_related('course', 'cohort').filter(
        course__aisl_extension__peer_review_enabled=True,
        cohort__mode='self_paced',
        cohort__is_active=True,
    )
    for project in projects:
        result = PeerReviewService.form_self_paced_batches_for_project(project)
        summary['batched'] += result['batched']
        summary['reviews_assigned'] += result['reviews_assigned']
    return summary
