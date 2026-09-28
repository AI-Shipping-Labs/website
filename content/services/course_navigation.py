"""Where a link to "the course" should take a given viewer.

An enrolled learner with course access works from the private course Home
(``/courses/<slug>/home``); everyone else gets the public landing page
(``/courses/<slug>``). Every in-app "open course" / "back to course" link and
the landing-page redirect resolve through :func:`course_entry_url` /
:func:`opens_course_home` so the rule lives in one place.

The public landing stays reachable for enrolled learners through
``?view=overview`` (see :func:`course_overview_url`).
"""

from __future__ import annotations

from urllib.parse import urlencode

from django.urls import reverse

from accounts.utils.user_checks import is_authenticated_user
from content.access import can_access
from content.services.enrollment import is_enrolled

HOME_SECTION_URL_NAMES = {
    'home': 'course_home',
    'syllabus': 'course_syllabus',
    'sessions': 'course_office_hours',
    'homework': 'course_homework',
    'projects': 'course_projects',
}

# Per-user memo so one page asking about the same course several times
# (breadcrumb, back link, completion CTA) costs one enrollment lookup.
_MEMO_ATTR = '_opens_course_home_memo'


def _cohort_query(cohort) -> str:
    key = getattr(cohort, 'external_key', cohort) or ''
    return f'?{urlencode({"cohort": key})}' if key else ''


def course_home_url(course, *, section: str = 'home', cohort='') -> str:
    """Return the course Home URL (or one of its tabs), keeping ``cohort``.

    ``cohort`` is the selected cohort key (the ``?cohort=`` value) or a
    ``Cohort`` instance; empty means no explicit selection.
    """
    url_name = HOME_SECTION_URL_NAMES[section]
    return reverse(url_name, kwargs={'slug': course.slug}) + _cohort_query(cohort)


def course_overview_url(course, *, cohort='') -> str:
    """Return the public landing URL that does not redirect enrolled users."""
    query = {'view': 'overview'}
    key = getattr(cohort, 'external_key', cohort) or ''
    if key:
        query['cohort'] = key
    return f'{course.get_absolute_url()}?{urlencode(query)}'


def opens_course_home(user, course, *, enrolled_course_ids=None) -> bool:
    """True when ``user`` should land on course Home instead of the landing.

    Requires an active enrollment and current access (a lapsed tier sends
    the learner back to the landing, where the upgrade path lives).
    ``enrolled_course_ids`` lets list views that already fetched the
    user's enrollments skip the per-course lookup.
    """
    if not is_authenticated_user(user):
        return False
    memo = getattr(user, _MEMO_ATTR, None)
    if memo is None:
        memo = {}
        setattr(user, _MEMO_ATTR, memo)
    if course.pk in memo:
        return memo[course.pk]
    if enrolled_course_ids is not None:
        enrolled = course.pk in enrolled_course_ids
    else:
        enrolled = is_enrolled(user, course)
    result = enrolled and can_access(user, course)
    memo[course.pk] = result
    return result


def course_entry_url(
    user, course, *, cohort='', section: str = 'home', enrolled_course_ids=None,
) -> str:
    """Return the URL a link to ``course`` should use for ``user``.

    Enrolled learners with access get course Home; anonymous and
    non-enrolled visitors get the public landing. Either way a selected
    cohort is kept (the landing uses it for its schedule preview).
    """
    if opens_course_home(user, course, enrolled_course_ids=enrolled_course_ids):
        return course_home_url(course, section=section, cohort=cohort)
    return course.get_absolute_url() + _cohort_query(cohort)
