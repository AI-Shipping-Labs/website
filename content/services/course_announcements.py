"""Course announcement visibility, publish, and email fan-out (issue #1942)."""

from __future__ import annotations

import logging

from django.core.paginator import Paginator
from django.db import transaction
from django.utils import timezone
from django.utils.safestring import mark_safe

from accounts.utils.user_checks import is_authenticated_user
from content.models.course_announcement import CourseAnnouncement
from content.models.enrollment import Enrollment
from content.services.enrollment import is_enrolled
from content.utils.markdown import render_markdown

logger = logging.getLogger(__name__)

EMAIL_TYPE = 'course_announcement'
EMAIL_BATCH_SIZE = 100
MEMBER_PAGE_SIZE = 20
PUBLISH_CONFIRM = (
    'Publish this announcement and email {n} enrolled learners? '
    'This emails the class once and cannot be undone.'
)


def show_announcements_tab(user, course) -> bool:
    """True when the Announcements tab should render for ``user``."""
    if not is_authenticated_user(user):
        return False
    return bool(getattr(user, 'is_staff', False) or is_enrolled(user, course))


def can_view_announcements(user, course) -> bool:
    """Same gate as the tab: enrolled learner or staff."""
    return show_announcements_tab(user, course)


def publish_confirm_message(course) -> str:
    from content.services.enrollment import active_enrollment_count

    return PUBLISH_CONFIRM.format(n=active_enrollment_count(course))


def published_queryset(course):
    return (
        CourseAnnouncement.objects
        .filter(course=course, status=CourseAnnouncement.STATUS_PUBLISHED)
        .select_related('author')
        .order_by('-published_at', '-created_at')
    )


def paginate_member_announcements(queryset, raw_page):
    """Clamp ``page`` like Pods and return (page, paginator)."""
    paginator = Paginator(queryset, MEMBER_PAGE_SIZE)
    total_pages = max(paginator.num_pages, 1)
    try:
        page_number = int(raw_page)
    except (TypeError, ValueError):
        page_number = 1
    page_number = min(max(page_number, 1), total_pages)
    return paginator.page(page_number), paginator


def attach_rendered_bodies(announcements):
    """Attach ``body_html`` from markdown for the member tab."""
    for announcement in announcements:
        announcement.body_html = mark_safe(render_markdown(announcement.body or ''))
    return announcements


def save_announcement(announcement, *, title, body, status, author=None):
    """Persist title/body/status and queue email on the first publish."""
    previous_status = (
        announcement.status
        if announcement.pk
        else CourseAnnouncement.STATUS_DRAFT
    )
    announcement.title = title
    announcement.body = body
    announcement.status = status
    if author is not None and announcement.author_id is None:
        announcement.author = author
    if (
        status == CourseAnnouncement.STATUS_PUBLISHED
        and announcement.published_at is None
    ):
        announcement.published_at = timezone.now()
    announcement.save()
    return maybe_enqueue_announcement_email(
        announcement, previous_status=previous_status,
    )


def apply_publish_transition(announcement, *, previous_status):
    """Publish a saved draft: set ``published_at``, save, queue email."""
    announcement.status = CourseAnnouncement.STATUS_PUBLISHED
    if announcement.published_at is None:
        announcement.published_at = timezone.now()
    announcement.save(update_fields=[
        'title', 'body', 'status', 'published_at', 'updated_at',
    ])
    return maybe_enqueue_announcement_email(
        announcement, previous_status=previous_status,
    )


def maybe_enqueue_announcement_email(announcement, *, previous_status):
    """Queue fan-out once, on the first draft -> published transition."""
    if announcement.status != CourseAnnouncement.STATUS_PUBLISHED:
        return False
    if previous_status == CourseAnnouncement.STATUS_PUBLISHED:
        return False
    if announcement.email_queued_at is not None:
        return False
    announcement_id = announcement.pk
    transaction.on_commit(
        lambda: enqueue_course_announcement_email(announcement_id),
    )
    return True


def enqueue_course_announcement_email(announcement_id):
    from jobs.tasks import async_task, build_task_name

    return async_task(
        'content.services.course_announcements.send_course_announcement_email',
        announcement_id,
        task_name=build_task_name(
            'Send course announcement',
            f'announcement #{announcement_id}',
            'first publish',
        ),
    )


def send_course_announcement_email(announcement_id):
    """Worker: claim ``email_queued_at`` once, then fan out in batches of 100."""
    with transaction.atomic():
        announcement = (
            CourseAnnouncement.objects
            .select_for_update()
            .select_related('course')
            .filter(pk=announcement_id)
            .first()
        )
        if announcement is None:
            return {
                'status': 'skipped',
                'reason': 'missing_announcement',
                'announcement_id': announcement_id,
            }
        if announcement.status != CourseAnnouncement.STATUS_PUBLISHED:
            return {
                'status': 'skipped',
                'reason': 'not_published',
                'announcement_id': announcement_id,
            }
        if announcement.email_queued_at is not None:
            return {
                'status': 'skipped',
                'reason': 'already_queued',
                'announcement_id': announcement_id,
            }
        announcement.email_queued_at = timezone.now()
        announcement.save(update_fields=['email_queued_at', 'updated_at'])

    sent = 0
    failed = 0
    enrollments = (
        Enrollment.objects
        .filter(
            course_id=announcement.course_id,
            unenrolled_at__isnull=True,
            user__is_active=True,
        )
        .select_related('user')
        .order_by('pk')
    )
    paginator = Paginator(enrollments, EMAIL_BATCH_SIZE)
    for page_number in paginator.page_range:
        for enrollment in paginator.page(page_number).object_list:
            if _deliver_one(announcement, enrollment.user):
                sent += 1
            else:
                failed += 1
    return {
        'status': 'queued',
        'announcement_id': announcement_id,
        'sent': sent,
        'failed': failed,
    }


def _deliver_one(announcement, user):
    from email_app.package_mail import send_package_mail

    if not getattr(user, 'is_active', False):
        return False
    try:
        send_package_mail(
            user,
            EMAIL_TYPE,
            {},
            related=announcement,
            idempotency_key=f'course-announcement:{announcement.pk}:{user.pk}',
        )
    except Exception:
        logger.exception(
            'Course announcement %s failed for user %s',
            announcement.pk,
            user.pk,
        )
        return False
    return True
