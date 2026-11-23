"""Course announcement visibility, publish, and email fan-out (issue #1942).

Every announcement targets one cohort of its course: only members of that
cohort see it on the Announcements tab, and its first publish emails only
the cohort's actively enrolled members. An announcement without a cohort
has no audience: learners never see it and publishing it sends nothing.
"""

from __future__ import annotations

import logging

from django.core.paginator import Paginator
from django.db import transaction
from django.utils import timezone
from django.utils.safestring import mark_safe

from accounts.utils.user_checks import is_authenticated_user
from content.models.cohort import Cohort, CohortEnrollment
from content.models.course_announcement import CourseAnnouncement
from content.models.enrollment import Enrollment
from content.services.enrollment import is_enrolled
from content.utils.markdown import render_markdown

logger = logging.getLogger(__name__)

EMAIL_TYPE = 'course_announcement'
EMAIL_BATCH_SIZE = 100
MEMBER_PAGE_SIZE = 20
PUBLISH_CONFIRM = (
    'Publish this announcement and email {n} enrolled learners of {cohort}? '
    'This emails the cohort once and cannot be undone.'
)
PUBLISH_CONFIRM_UNKNOWN = (
    'Publish this announcement and email the enrolled learners of the '
    'selected cohort? This emails the cohort once and cannot be undone.'
)


def show_announcements_tab(user, course) -> bool:
    """True when the Announcements tab should render for ``user``."""
    if not is_authenticated_user(user):
        return False
    return bool(getattr(user, 'is_staff', False) or is_enrolled(user, course))


def can_view_announcements(user, course) -> bool:
    """Same gate as the tab: enrolled learner or staff."""
    return show_announcements_tab(user, course)


def recipient_enrollments(cohort):
    """Active course enrollments of active users who belong to ``cohort``."""
    if cohort is None:
        return Enrollment.objects.none()
    return Enrollment.objects.filter(
        course_id=cohort.course_id,
        unenrolled_at__isnull=True,
        user__is_active=True,
        user__cohort_enrollments__cohort=cohort,
    ).distinct()


def course_active_enrollment_count(course_id) -> int:
    """Active enrollments of active users across every cohort of a course.

    Shown next to the cohort count in the recipient preview so an operator
    can see the whole-course number the cohort scope protects against.
    """
    return Enrollment.objects.filter(
        course_id=course_id,
        unenrolled_at__isnull=True,
        user__is_active=True,
    ).count()


def announcement_blocked_reason(announcement, cohort):
    """Why publishing ``announcement`` for ``cohort`` would email nobody."""
    if announcement.email_queued_at is not None:
        return 'already_emailed'
    if announcement.status == CourseAnnouncement.STATUS_PUBLISHED:
        return 'already_published'
    if cohort is None:
        return 'no_cohort'
    return None


def recipient_preview(announcement, *, cohort=None):
    """Read-only recipient summary for publishing ``announcement``.

    ``cohort`` previews a different cohort than the stored one (the cohort
    a publish call would set); ``None`` uses the announcement's own cohort.
    Never writes and never queues email.
    """
    target = cohort if cohort is not None else announcement.cohort
    blocked = announcement_blocked_reason(announcement, target)
    recipients = recipient_enrollments(target).count() if target else 0
    return {
        'announcement_id': announcement.pk,
        'course': announcement.course.slug,
        'status': announcement.status,
        'cohort': None if target is None else {
            'id': target.pk,
            'external_key': target.external_key,
            'name': target.name,
        },
        'stored_cohort_id': announcement.cohort_id,
        'recipient_count': recipients,
        'course_active_enrollment_count': course_active_enrollment_count(
            announcement.course_id,
        ),
        'course_cohort_count': Cohort.objects.filter(
            course_id=announcement.course_id,
        ).count(),
        'email_queued_at': announcement.email_queued_at,
        'will_email': blocked is None,
        'blocked_reason': blocked,
    }


def publish_confirm_message(cohort=None) -> str:
    if cohort is None:
        return PUBLISH_CONFIRM_UNKNOWN
    return PUBLISH_CONFIRM.format(
        n=recipient_enrollments(cohort).count(), cohort=cohort.name,
    )


def published_queryset(course, *, user=None, cohort=None):
    """Published announcements of ``course`` visible to ``user``.

    Learners see only announcements of the cohorts they belong to. Staff
    see every cohort's announcements, or only ``cohort``'s when they
    preview one. ``user=None`` skips the audience filter.
    """
    rows = CourseAnnouncement.objects.filter(
        course=course, status=CourseAnnouncement.STATUS_PUBLISHED,
    )
    if user is not None:
        if getattr(user, 'is_staff', False):
            if cohort is not None:
                rows = rows.filter(cohort=cohort)
        else:
            rows = rows.filter(cohort_id__in=CohortEnrollment.objects.filter(
                user=user, cohort__course=course,
            ).values('cohort_id'))
    return (
        rows
        .select_related('author', 'cohort')
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


def save_announcement(
    announcement, *, title, body, status, author=None, cohort=None,
):
    """Persist title/body/status/cohort and queue email on the first publish.

    ``cohort=None`` keeps the announcement's current cohort.
    """
    previous_status = (
        announcement.status
        if announcement.pk
        else CourseAnnouncement.STATUS_DRAFT
    )
    announcement.title = title
    announcement.body = body
    announcement.status = status
    if cohort is not None:
        announcement.cohort = cohort
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
    if announcement.cohort_id is None:
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
            .select_related('course', 'cohort')
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
        if announcement.cohort_id is None:
            return {
                'status': 'skipped',
                'reason': 'no_cohort',
                'announcement_id': announcement_id,
            }
        announcement.email_queued_at = timezone.now()
        announcement.save(update_fields=['email_queued_at', 'updated_at'])

    sent = 0
    failed = 0
    enrollments = (
        recipient_enrollments(announcement.cohort)
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
        'cohort_id': announcement.cohort_id,
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
