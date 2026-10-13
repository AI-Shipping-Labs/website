"""The course Home ``Pods`` tab, the shared course header context, and the
pods page frame for cohort and sprint pods."""

from urllib.parse import urlencode

from content.services.course_announcements import show_announcements_tab
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
    context['show_announcements_tab'] = show_announcements_tab(user, course)
    return context


def pods_frame_context(activity, user):
    """The page frame for a pods page (issue #1921).

    Cohort pods keep the course Home header and tab strip. Sprint pods get a
    sprint header (``Sprint`` eyebrow, the sprint name linking back to the
    sprint page) and no course tabs. Both carry ``pod_activity`` so the
    templates read URLs and copy from one place.
    """
    if activity.is_sprint:
        context = {
            'sprint': activity.sprint,
            'section': 'pods',
            'pods_frame_sprint': True,
            'pods_frame_title': activity.sprint.name,
            'pods_frame_studio_obj': activity.sprint,
            'sprint_url': activity.home_url(),
        }
    else:
        cohort = activity.cohort
        context = course_header_context(cohort.course, user, cohort)
        context.update({
            'pods_frame_sprint': False,
            'pods_frame_title': cohort.course.title,
            'pods_frame_studio_obj': cohort.course,
        })
    context.update({
        'pod_activity': activity,
        'pods_copy': activity.copy,
    })
    return context
