"""Enrollment service helpers — issue #236.

Centralises the "ensure an active enrollment exists" logic so views,
the auto-enroll-on-progress hook, and the Studio admin all behave the
same way.
"""

from __future__ import annotations

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
    if user is None or not getattr(user, 'is_authenticated', False):
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
    if user is None or not getattr(user, 'is_authenticated', False):
        return None, False

    existing = get_active_enrollment(user, course)
    if existing is not None:
        return existing, False

    enrollment = Enrollment.objects.create(
        user=user,
        course=course,
        source=source,
    )
    return enrollment, True


def auto_enroll_on_progress(user, course):
    """Hook for the mark-complete view — enroll if not already enrolled.

    Thin wrapper around ``ensure_enrollment(..., source=auto_progress)``;
    exists so callers read clearly.
    """
    return ensure_enrollment(user, course, source=SOURCE_AUTO_PROGRESS)


def unenroll(user, course) -> bool:
    """Soft-delete the active enrollment. Returns True if anything changed."""
    from django.utils import timezone

    enrollment = get_active_enrollment(user, course)
    if enrollment is None:
        return False
    enrollment.unenrolled_at = timezone.now()
    enrollment.save(update_fields=['unenrolled_at'])
    return True
