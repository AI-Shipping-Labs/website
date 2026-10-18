"""Selected/default cohort progress context for course HTTP surfaces."""

from content.services.course_commitments import is_self_paced_view
from content.services.course_schedule import select_display_cohort
from content.services.course_units import build_course_unit_navigation_context
from content.services.course_view_units import (
    CourseProgressCounts,
    course_progress_counts,
)


def select_course_view(course, user, requested_key=''):
    cohort, is_preview = select_display_cohort(course, user, requested_key)
    self_paced = is_self_paced_view(course, user, cohort)
    return cohort, is_preview, self_paced


def default_course_progress(user, course):
    if not user.is_authenticated:
        return CourseProgressCounts(completed=0, total=0)
    _cohort, _is_preview, self_paced = select_course_view(course, user)
    return course_progress_counts(user, course, self_paced)


def build_course_view_navigation_context(
    user, course, module, unit, *, request, selected_cohort,
):
    self_paced = is_self_paced_view(course, user, selected_cohort)
    return build_course_unit_navigation_context(
        user, course, module, unit, request=request,
        session_cohort=selected_cohort, self_paced_view=self_paced,
    )
