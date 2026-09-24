"""Enrollment service helpers — issue #236.

Centralises the "ensure an active enrollment exists" logic so views,
the auto-enroll-on-progress hook, and the Studio admin all behave the
same way.
"""

from __future__ import annotations

from accounts.utils.user_checks import is_authenticated_user
from content.models.cohort import CohortEnrollment
from content.models.enrollment import (
    SOURCE_AUTO_PROGRESS,
    SOURCE_MANUAL,
    Enrollment,
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


def unenroll(user, course) -> bool:
    """Soft-delete the active enrollment. Returns True if anything changed."""
    from django.utils import timezone

    enrollment = get_active_enrollment(user, course)
    if enrollment is None:
        return False
    enrollment.unenrolled_at = timezone.now()
    enrollment.save(update_fields=['unenrolled_at'])
    return True
