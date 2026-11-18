"""Peer-review notification delivery boundaries."""

import logging

logger = logging.getLogger(__name__)

REVIEW_ASSIGNED_EMAIL_TYPE = 'coursework.review_assigned'


def _review_list_path(course, project):
    if project is not None:
        return f'/courses/{course.slug}/projects/{project.slug}/reviews'
    return f'/courses/{course.slug}/reviews'


def _batch_email_context(course, review_count, deadline, project):
    project_slug = None
    project_title = course.title
    if project is not None:
        project_slug = project.slug
        project_title = project.title
    due_date = None
    if deadline is not None:
        due_date = deadline.isoformat()
    return {
        'course_slug': course.slug,
        'project_slug': project_slug,
        'project_title': project_title,
        'review_count': review_count,
        'due_date': due_date,
    }


def notify_batch_ready_email(course, submission, review_count, deadline, project=None):
    """Queue the coursework.review_assigned email for one batched reviewer."""
    user = submission.user
    if not (getattr(user, 'email', '') or '').strip():
        return None
    from email_app.package_mail import send_package_mail

    context = _batch_email_context(course, review_count, deadline, project)
    # Broad catch: email delivery must not abort batch assignment.
    try:
        return send_package_mail(
            user,
            REVIEW_ASSIGNED_EMAIL_TYPE,
            context,
            idempotency_key=f'{REVIEW_ASSIGNED_EMAIL_TYPE}:{submission.pk}',
            related=submission,
            category='coursework',
        )
    except Exception:
        logger.exception(
            'Failed to send %s email for submission %s',
            REVIEW_ASSIGNED_EMAIL_TYPE,
            submission.pk,
        )
        return None


def notify_batch_ready(course, submissions, deadline, body_builder, project=None):
    """Send on-platform notifications and emails when a batch is formed."""
    from content.models import PeerReview
    from notifications.models import Notification

    url = _review_list_path(course, project)
    for submission in submissions:
        review_count = PeerReview.objects.filter(
            reviewer=submission.user,
            submission__course=course,
            submission__course_project=project,
            is_complete=False,
        ).count()
        # Broad catch: notification delivery must not abort batch assignment.
        try:
            Notification.objects.create(
                user=submission.user,
                title=f'Peer reviews ready: {course.title}',
                body=body_builder(review_count, deadline),
                url=url,
                notification_type='new_content',
            )
        except Exception:
            logger.exception(
                'Failed to send batch ready notification for submission %s',
                submission.pk,
            )
        notify_batch_ready_email(
            course, submission, review_count, deadline, project=project,
        )


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
