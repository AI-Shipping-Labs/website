"""The course Home ``Pods`` tab and the shared course header context."""

from urllib.parse import urlencode

from pods.services.config import pods_enabled_for_course
from pods.services.people import can_view_cohort_pods, is_dated_cohort
from pods.services.presentation import pending_count_for_owner


def pods_tab_context(course, user, cohort):
    """``show_pods_tab`` for dated-cohort viewers of an enabled course, plus
    the owner's pending-request count for the tab badge."""
    visible = (
        pods_enabled_for_course(course)
        and is_dated_cohort(cohort)
        and can_view_cohort_pods(user, cohort)
    )
    return {
        'show_pods_tab': visible,
        'pods_tab_count': pending_count_for_owner(cohort, user) if visible else 0,
    }


def course_header_context(course, user, cohort):
    """Header and tab-strip context the pods pages share with course Home."""
    context = {
        'course': course,
        'cohort': cohort,
        'section': 'pods',
        'self_paced_view': False,
        'home_enrollment_count': cohort.enrollment_count,
        'cohort_query': f'?{urlencode({"cohort": cohort.external_key})}' if cohort.external_key else '',
    }
    if user.is_staff:
        context['preview_cohorts'] = course.aisl_cohorts.filter(
            mode='cohort', is_active=True,
        ).order_by('start_date')
    context.update(pods_tab_context(course, user, cohort))
    return context
