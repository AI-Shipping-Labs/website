"""Batched Continue Learning course cards for the member dashboard."""

from content.models import CourseAccess, Enrollment, Unit, UserCourseProgress
from content.services.course_navigation import course_home_url
from content.services.course_schedule import default_display_cohorts_by_course
from content.services.course_view_units import unit_visible_in_course_view


def _enrollment_maps(user):
    enrollments = list(Enrollment.objects.filter(
        user=user, unenrolled_at__isnull=True,
    ).select_related('course'))
    courses = {}
    enrolled_at = {}
    for enrollment in enrollments:
        courses[enrollment.course_id] = enrollment.course
        enrolled_at[enrollment.course_id] = enrollment.enrolled_at
    return courses, enrolled_at


def _self_paced_course_ids(user, course_ids):
    cohorts = default_display_cohorts_by_course(user, course_ids)
    result = set()
    for course_id, cohort in cohorts.items():
        if cohort is not None and cohort.mode == 'self_paced':
            result.add(course_id)
    return result


def _progress_maps(user, course_ids, self_paced_ids):
    completed = {}
    last_at = {}
    last_unit = {}
    for course_id in course_ids:
        completed[course_id] = set()
    rows = UserCourseProgress.objects.filter(
        user=user, unit__module__course_id__in=course_ids,
        completed_at__isnull=False,
    ).select_related('unit__module__parent')
    for progress in rows:
        course_id = progress.unit.module.course_id
        if not _unit_counts_for_dashboard(progress.unit, course_id, self_paced_ids):
            continue
        completed[course_id].add(progress.unit_id)
        if course_id not in last_at or progress.completed_at > last_at[course_id]:
            last_at[course_id] = progress.completed_at
            last_unit[course_id] = progress.unit
    return completed, last_at, last_unit


def _unit_counts_for_dashboard(unit, course_id, self_paced_ids):
    if course_id not in self_paced_ids:
        return True
    return unit_visible_in_course_view(unit, True) and not unit.effective_is_bonus


def _units_by_course(course_ids, self_paced_ids):
    result = {}
    for course_id in course_ids:
        result[course_id] = []
    units = Unit.objects.filter(
        module__course_id__in=course_ids,
    ).select_related('module__course', 'module__parent').order_by(
        'module__sort_order', 'sort_order', 'pk',
    )
    for unit in units:
        course_id = unit.module.course_id
        if _unit_counts_for_dashboard(unit, course_id, self_paced_ids):
            result[course_id].append(unit)
    return result


def _granted_course_ids(user, course_ids):
    return set(CourseAccess.objects.filter(
        user=user, course_id__in=course_ids,
    ).values_list('course_id', flat=True))


def _next_unit(units, completed_ids):
    for unit in units:
        if unit.pk not in completed_ids:
            return unit
    return None


def _card(course, units, completed_ids, enrolled_at, last_at, last_unit):
    total = len(units)
    completed_count = len(completed_ids)
    if total == 0 or completed_count >= total:
        return None
    completed_at = last_at.get(course.pk)
    sort_key = completed_at or enrolled_at[course.pk]
    return {
        'kind': 'course', 'course': course,
        'completed_count': completed_count, 'total_units': total,
        'percentage': int((completed_count / total) * 100),
        'last_unit': last_unit.get(course.pk),
        'last_completed_at': completed_at,
        'next_unit': _next_unit(units, completed_ids),
        'home_url': course_home_url(course),
        'enrolled_at': enrolled_at[course.pk], '_sort_key': sort_key,
    }


def in_progress_courses(user, user_level):
    courses, enrolled_at = _enrollment_maps(user)
    if not courses:
        return []
    course_ids = list(courses)
    self_paced_ids = _self_paced_course_ids(user, course_ids)
    completed, last_at, last_unit = _progress_maps(
        user, course_ids, self_paced_ids,
    )
    units = _units_by_course(course_ids, self_paced_ids)
    granted = _granted_course_ids(user, course_ids)
    cards = []
    for course_id, course in courses.items():
        if course.required_level > user_level and course_id not in granted:
            continue
        card = _card(
            course, units[course_id], completed[course_id],
            enrolled_at, last_at, last_unit,
        )
        if card is not None:
            cards.append(card)
    cards.sort(key=lambda item: item['_sort_key'], reverse=True)
    for card in cards:
        del card['_sort_key']
    return cards
