"""Peer-review notification delivery boundaries."""

import logging

logger = logging.getLogger(__name__)


def notify_reviews_complete(submission):
    """Notify the submitter without disrupting the review transition."""
    course = submission.course
    url = f'/courses/{course.slug}/reviews'
    if submission.course_project_id:
        url = (
            f'/courses/{course.slug}/projects/'
            f'{submission.course_project.slug}/reviews'
        )
    # Notification delivery is non-critical to the completed review transition.
    try:
        from notifications.models import Notification
        Notification.objects.create(
            user=submission.user,
            title=f'Reviews complete: {course.title}',
            body='All peer reviews for your project are in. View your feedback.',
            url=url,
            notification_type='new_content',
        )
    except Exception:
        logger.exception('Failed to send reviews complete notification')
