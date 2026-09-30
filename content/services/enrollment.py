"""Enrollment service helpers — issue #236.

Centralises the "ensure an active enrollment exists" logic so views,
the auto-enroll-on-progress hook, and the Studio admin all behave the
same way.
"""

from __future__ import annotations

import logging

from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from accounts.utils.user_checks import is_authenticated_user
from content.models.cohort import CohortEnrollment
from content.models.enrollment import (
    SOURCE_AUTO_PROGRESS,
    SOURCE_MANUAL,
    Enrollment,
)

logger = logging.getLogger(__name__)

# Why a learner stopped being enrolled; carried into the CRM activity row
# and the staff heads-up.
UNENROLL_CAUSE_SELF = 'self'
UNENROLL_CAUSE_STAFF = 'staff'
UNENROLL_CAUSE_ACCESS_LOST = 'access_lost'
UNENROLL_CAUSES = (
    UNENROLL_CAUSE_SELF,
    UNENROLL_CAUSE_STAFF,
    UNENROLL_CAUSE_ACCESS_LOST,
)


def get_active_enrollment(user, course):
    """Return the user's active Enrollment for ``course``, or None.

    Treats ``unenrolled_at IS NULL`` as the active marker. Anonymous /
    None users return None.
    """
    if not is_authenticated_user(user):
        return None
    return (
        Enrollment.objects
        .filter(user=user, course=course, unenrolled_at__isnull=True)
        .first()
    )


def is_enrolled(user, course) -> bool:
    """Return True if the user has an active enrollment in ``course``."""
    return get_active_enrollment(user, course) is not None


def ensure_enrollment(user, course, source: str = SOURCE_MANUAL):
    """Create an active Enrollment for (user, course) if one doesn't exist.

    Idempotent — if an active enrollment already exists, returns it
    without modifying ``source``. Returns ``(enrollment, created)``.

    Use ``source=SOURCE_AUTO_PROGRESS`` from the mark-complete hook so
    we can distinguish "user clicked Enroll" from "user marked a lesson
    complete and we backed into an enrollment".
    """
    if not is_authenticated_user(user):
        return None, False

    existing = get_active_enrollment(user, course)
    if existing is not None:
        return existing, False

    enrollment = Enrollment.objects.create(
        user=user,
        course=course,
        source=source,
    )

    # Record a `course_enroll` activity row for the CRM timeline
    # (issue #853). Defensive — never raises into the enroll path.
    from analytics.activity import record_course_enroll

    record_course_enroll(user, course)

    return enrollment, True


def auto_enroll_on_progress(user, course):
    """Hook for the mark-complete view — enroll if not already enrolled.

    Thin wrapper around ``ensure_enrollment(..., source=auto_progress)``;
    exists so callers read clearly.
    """
    return ensure_enrollment(user, course, source=SOURCE_AUTO_PROGRESS)


def ensure_self_paced_cohort_enrollment(user, course):
    """Idempotently enroll ``user`` into ``course``'s self-paced Cohort.

    When a user has course access and holds no ``CohortEnrollment`` in a
    dated cohort, courses without a dated cohort receive a generated
    self-paced cohort and membership — mirroring the
    ``get_or_create`` idempotency ``ensure_enrollment`` already uses for
    course-level ``Enrollment``. A course with a dated cohort never gets a
    generated self-paced cohort, and the learner is never auto-joined to
    its real cohort.

    A no-op (returns ``None``) for anonymous users, for users already
    enrolled in a dated cohort of this course, and for courses with a real
    dated cohort but no enrollment.
    """
    if not is_authenticated_user(user):
        return None

    from content.access import can_access

    if not can_access(user, course):
        return None

    has_dated_enrollment = CohortEnrollment.objects.filter(
        user=user, cohort__course=course, cohort__mode='cohort',
    ).exists()
    if has_dated_enrollment:
        return None

    from content.services.course_cohorts import ensure_course_self_paced_cohort

    self_paced_cohort = ensure_course_self_paced_cohort(course)
    if self_paced_cohort is None:
        return None

    enrollment, _created = CohortEnrollment.objects.get_or_create(
        cohort=self_paced_cohort, user=user,
    )
    return enrollment


def active_enrollment_counts(course_ids) -> dict[int, int]:
    """Return ``{course_id: active enrollment count}`` in one query.

    The single owner of "how many people are enrolled in this course":
    an active ``Enrollment`` row (``unenrolled_at IS NULL``) per learner.
    Courses with no active enrollments are absent from the mapping, so
    callers read ``counts.get(course_id, 0)``. Cohort-level counts stay
    on ``Cohort.enrollment_count``.
    """
    course_ids = [course_id for course_id in course_ids if course_id is not None]
    if not course_ids:
        return {}
    rows = (
        Enrollment.objects
        .filter(course_id__in=course_ids, unenrolled_at__isnull=True)
        .values('course_id')
        .annotate(total=Count('id'))
    )
    return {row['course_id']: row['total'] for row in rows}


def active_enrollment_count(course) -> int:
    """Return the number of active enrollments in ``course``."""
    return active_enrollment_counts([course.pk]).get(course.pk, 0)


def unenroll(user, course, *, cause=None, actor=None) -> bool:
    """Soft-delete the active enrollment. Returns True if anything changed.

    Every real change records the ``course_unenroll`` CRM activity and
    queues the staff heads-up (``record_unenrollment``). ``cause`` is one
    of ``UNENROLL_CAUSE_*``; it defaults to the learner acting on their
    own enrollment.

    Leaving the course also leaves its dated cohorts (membership, cohort
    tags, cohort series registration) so cohort rosters, counts and cohort
    emails agree with the course. Re-enrolling does not restore them; the
    learner picks a cohort again. The one notification names the cohort
    they left.
    """
    enrollment = get_active_enrollment(user, course)
    if enrollment is None:
        return False
    # Inline, as in ``ensure_self_paced_cohort_enrollment``:
    # ``course_cohorts`` imports events models, and this module is imported
    # while those apps load.
    from content.services.course_cohorts import remove_dated_cohort_memberships

    # Read before the memberships are removed, for the notification.
    cohort = _dated_cohort_for(user, course)
    with transaction.atomic():
        enrollment.unenrolled_at = timezone.now()
        enrollment.save(update_fields=['unenrolled_at'])
        remove_dated_cohort_memberships(user, course)
    record_unenrollment(
        user, course,
        cohort=cohort,
        cause=cause or UNENROLL_CAUSE_SELF,
        actor=actor,
    )
    return True


# --- Unenroll notifications ---

def _dated_cohort_for(user, course):
    """Return the user's dated cohort in ``course``, or None."""
    membership = (
        CohortEnrollment.objects
        .filter(user=user, cohort__course=course, cohort__mode='cohort')
        .select_related('cohort')
        .order_by('-cohort__start_date')
        .first()
    )
    return membership.cohort if membership else None


def record_course_access_loss(user, course, *, actor=None) -> bool:
    """Record an enrolled learner losing access to ``course``.

    Call after a course grant is removed. When the learner still holds an
    active enrollment but can no longer open the course, record the
    ``access_lost`` unenroll (activity row plus staff heads-up) and return
    True. The enrollment row itself is kept, so restoring access restores
    the course. Returns False when nothing was lost.
    """
    # Inline, as in ``ensure_self_paced_cohort_enrollment``.
    from content.access import can_access

    if not is_enrolled(user, course) or can_access(user, course):
        return False
    record_unenrollment(
        user, course,
        cohort=_dated_cohort_for(user, course),
        cause=UNENROLL_CAUSE_ACCESS_LOST,
        actor=actor,
    )
    return True


def record_unenrollment(user, course, *, cohort=None, cause, actor=None, notify=True):
    """Record that ``user`` left ``course`` (or one of its cohorts).

    The single owner for every unenroll path: writes the
    ``course_unenroll`` CRM activity row and, unless ``notify`` is False,
    queues the staff Slack heads-up in the background so a slow Slack
    never delays the learner's or operator's request. Never raises.
    """
    # Inline like ``ensure_enrollment``'s ``record_course_enroll``:
    # ``analytics.activity`` imports content and events models, and this
    # module is imported while those apps load.
    from analytics.activity import record_course_unenroll

    record_course_unenroll(user, course, cohort=cohort, cause=cause)
    if not notify:
        return
    try:
        # Inline for the same app-loading reason; ``jobs.tasks`` pulls in
        # django-q models.
        from jobs.tasks import async_task, build_task_name

        async_task(
            'community.services.staff_notifications.notify_course_unenroll',
            user_id=user.pk,
            course_id=course.pk,
            cohort_id=getattr(cohort, 'pk', None),
            cause=cause,
            actor_id=getattr(actor, 'pk', None),
            task_name=build_task_name(
                'Notify staff of course unenroll',
                f'user #{user.pk}',
                course.slug,
            ),
        )
    except Exception:
        # Intentional broad catch: the unenroll itself already happened;
        # a queue failure must not turn it into an error page.
        logger.exception(
            'Failed to queue course unenroll notification user=%s course=%s',
            user.pk, course.pk,
        )
