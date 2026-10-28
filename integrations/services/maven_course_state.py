"""Course state owned by a Maven enrollment occurrence."""

from content.models import CourseAccess
from content.models.cohort import CohortEnrollment
from content.models.enrollment import SOURCE_MAVEN
from content.services.course_cohorts import apply_cohort_enrollment_tags
from content.services.enrollment import (
    end_provider_enrollment,
    ensure_provider_enrollment,
)


def grant_maven_course_state(row, course, cohort, actions):
    """Idempotently create access, course enrollment, and cohort membership."""
    CourseAccess.objects.get_or_create(
        user=row.user, course=course,
        defaults={'access_type': 'granted'},
    )
    actions.append(f'Granted course access to {course.slug}.')
    ensure_provider_enrollment(row.user, course, source=SOURCE_MAVEN)
    actions.append(f'Enrolled in course {course.slug}.')
    CohortEnrollment.objects.get_or_create(cohort=cohort, user=row.user)
    actions.append(f'Enrolled in cohort {cohort.external_key}.')
    tags = apply_cohort_enrollment_tags(row.user, cohort)
    if tags:
        actions.append(f"Applied cohort contact tags: {', '.join(tags)}.")
    _register_event_series(row.user, cohort, actions)


def _register_event_series(user, cohort, actions):
    series = _cohort_event_series(cohort)
    if series is None:
        return
    from events.services.registration import get_or_create_series_registration

    get_or_create_series_registration(series, user)
    actions.append("Registered for the cohort's office-hours series.")


def _cohort_event_series(cohort):
    """Return the cohort's linked ``EventSeries``, or ``None``."""
    if not getattr(cohort, 'event_series_id', None):
        return None
    return getattr(cohort, 'event_series', None)


def revoke_maven_course_access(row, course, actions):
    """Revoke Maven-owned direct access and enrollment; return outcome copy."""
    deleted, _counts = CourseAccess.objects.filter(
        user_id=row.user_id, course=course, access_type='granted',
        granted_by__isnull=True,
    ).delete()
    if deleted:
        actions.append('Revoked course access.')
        outcome = f'Revoked access to {course.title}.'
    else:
        outcome = None
    retained = CourseAccess.objects.filter(
        user_id=row.user_id, course=course,
    ).first()
    if retained is not None and retained.access_type == 'purchased':
        return 'Kept course access: they bought the course directly.'
    if retained is not None:
        return 'Kept course access: they have an independent course grant.'
    if _membership_retains_course_access(row.user, course):
        return 'Kept course access: their membership still includes this course.'
    if end_provider_enrollment(row.user, course, source=SOURCE_MAVEN):
        actions.append('Ended course enrollment.')
    return outcome


def _membership_retains_course_access(user, course):
    """True for paid/manual tier access, excluding public readability."""
    from content.access import (
        LEVEL_BASIC,
        active_overrides_queryset,
        get_user_level,
    )

    if course.access_mode == 'entitlement' or course.required_level < LEVEL_BASIC:
        return False
    independent_override = (
        active_overrides_queryset()
        .filter(user=user)
        .exclude(source__startswith='maven:')
        .first()
    )
    return get_user_level(
        user, active_override=independent_override,
    ) >= course.required_level
