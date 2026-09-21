"""Site hook: individual CourseAccess rows grant package curriculum reads."""

# Delay model import until the hook runs; this module loads during app setup.
# ruff: noqa: PLC0415


def course_access_grants(user, course) -> bool:
    """Return True when this user holds a purchase or grant for ``course``."""
    if user is None or not getattr(user, 'is_authenticated', False):
        return False
    from content.models import CourseAccess

    return CourseAccess.objects.filter(user=user, course_id=course.pk).exists()
