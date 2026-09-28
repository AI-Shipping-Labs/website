"""Staff "Open in Studio" destinations for public pages.

Course and homework units jump to the operator surface for the page the
staff member is looking at. Every other object keeps ``get_studio_edit_url``.
"""

from django.urls import reverse

from content.models import UNIT_KIND_HOMEWORK, CohortEnrollment, Course, Unit
from content.models.homework import Homework
from content.services.course_schedule import select_display_cohort
from content.services.course_units import resolve_homework_for_unit

# Explicit ``section`` argument wins over the request URL name.
_SECTION_DESTINATIONS = {
    'home': 'homework',
    'homework': 'homework',
    'projects': 'peer_reviews',
    'sessions': 'cohorts',
    'syllabus': 'edit',
}

_URL_NAME_DESTINATIONS = {
    'course_detail': 'homework',
    'course_home': 'homework',
    'course_homework': 'homework',
    'course_projects': 'peer_reviews',
    'course_project_submit': 'peer_reviews',
    'course_project_reviews': 'peer_reviews',
    'course_project_review_form': 'peer_reviews',
    'project_submit': 'peer_reviews',
    'peer_review_dashboard': 'peer_reviews',
    'peer_review_form': 'peer_reviews',
    'course_office_hours': 'cohorts',
    'module_overview': 'edit',
    'course_syllabus': 'edit',
}


def studio_open_url(request, obj, section=''):
    """Return the Studio href for ``obj`` on this request, or ''."""
    if obj is None:
        return ''
    if isinstance(obj, Unit):
        return _unit_open_url(request, obj)
    if isinstance(obj, Course):
        return _course_open_url(request, obj, section or '')
    getter = getattr(obj, 'get_studio_edit_url', None)
    if getter is None:
        return ''
    url = getter() if callable(getter) else getter
    return url or ''


def _unit_open_url(request, unit):
    if unit.kind != UNIT_KIND_HOMEWORK:
        return _call_studio_edit_url(unit)
    course = unit.module.course
    user = _request_user(request)
    cohort = _owned_display_cohort(request, course)
    homework = None
    if cohort is not None:
        homework = resolve_homework_for_unit(unit, user, cohort=cohort)
    else:
        # No dated enrollment to display. Use a real enrollment only.
        # Do not fall through to another cohort's homework row.
        homework = _enrolled_homework(unit, user, course)
    if homework is not None:
        return reverse(
            'studio_homework_submissions', kwargs={'homework_id': homework.pk},
        )
    return reverse('studio_homework_list', kwargs={'course_id': course.pk})


def _owned_display_cohort(request, course):
    """Enrolled display cohort. A ``?cohort=`` preview does not win.

    ``select_display_cohort`` marks a staff ``?cohort=`` lookup as a
    preview even when that cohort is not the viewer's enrollment. Drop
    the query and resolve the enrollment again so the preview cannot
    choose another cohort's submissions.
    """
    user = _request_user(request)
    if user is None or not getattr(user, 'is_authenticated', False):
        return None
    requested = ''
    if request is not None and getattr(request, 'GET', None) is not None:
        requested = request.GET.get('cohort', '') or ''
    cohort, is_preview = select_display_cohort(course, user, requested)
    if cohort is not None and not is_preview:
        return cohort
    if requested:
        cohort, is_preview = select_display_cohort(course, user, '')
        if cohort is not None and not is_preview:
            return cohort
    return None


def _enrolled_homework(unit, user, course):
    """Homework on a cohort this user is enrolled in, or None.

    Self-paced enrollments are invisible to ``select_display_cohort``.
    This lookup does not use the unenrolled past-cohort fallback.
    """
    if user is None or not getattr(user, 'is_authenticated', False):
        return None
    if not getattr(unit, 'content_id', None):
        return None
    enrollment = (
        CohortEnrollment.objects
        .filter(user=user, cohort__course=course)
        .select_related('cohort')
        .first()
    )
    if enrollment is None:
        return None
    return (
        Homework.objects
        .filter(content_id=unit.content_id, cohort=enrollment.cohort)
        .first()
    )


def _request_user(request):
    if request is None:
        return None
    return getattr(request, 'user', None)


def _course_open_url(request, course, section):
    destination = _SECTION_DESTINATIONS.get(section)
    if destination is None:
        url_name = ''
        match = getattr(request, 'resolver_match', None) if request is not None else None
        if match is not None:
            url_name = match.url_name or ''
        destination = _URL_NAME_DESTINATIONS.get(url_name, 'homework')
    course_id = course.pk
    if destination == 'peer_reviews':
        return reverse(
            'studio_peer_review_management', kwargs={'course_id': course_id},
        )
    if destination == 'cohorts':
        return reverse(
            'studio_course_cohort_list', kwargs={'course_id': course_id},
        )
    if destination == 'edit':
        return reverse('studio_course_edit', kwargs={'course_id': course_id})
    return reverse('studio_homework_list', kwargs={'course_id': course_id})


def _call_studio_edit_url(obj):
    getter = getattr(obj, 'get_studio_edit_url', None)
    if getter is None:
        return ''
    url = getter() if callable(getter) else getter
    return url or ''
