"""Viewer-specific course-unit policy for progress and navigation."""

from collections import Counter
from dataclasses import dataclass

from content.models import Unit, UserCourseProgress
from content.models.course import UNIT_KIND_EVENT, non_bonus_units


@dataclass(frozen=True)
class CourseProgressCounts:
    completed: int
    total: int


def _required_first(items):
    required = []
    optional = []
    for item in items:
        if item.is_bonus:
            optional.append(item)
        else:
            required.append(item)
    return required + optional


def get_units_ordered_from_syllabus(modules):
    """Flatten a prefetched syllabus in the canonical reading order."""
    units = []
    for module in modules:
        children = _required_first(module.children.all())
        if children:
            for child in children:
                units.extend(_required_first(child.units.all()))
        else:
            units.extend(_required_first(module.units.all()))
    return units


def get_all_units_ordered(course):
    return get_units_ordered_from_syllabus(course.get_syllabus())


def unit_visible_in_course_view(unit, self_paced_view):
    """Return whether the selected viewer path includes one authored unit."""
    return not self_paced_view or unit.kind != UNIT_KIND_EVENT


def course_units_for_view(units, self_paced_view):
    """Exclude event placeholders only from an actual Self-paced view."""
    if not self_paced_view:
        return units
    visible = []
    for unit in units:
        if unit_visible_in_course_view(unit, True):
            visible.append(unit)
    return visible


def required_course_units_for_view(course, self_paced_view):
    """Return the required progress denominator for one course view."""
    units = non_bonus_units(Unit.objects.filter(module__course=course))
    if self_paced_view:
        units = units.exclude(kind=UNIT_KIND_EVENT)
    return units


def adjacent_viewer_unit(units, current_unit, step, self_paced_view):
    current_index = None
    for index, candidate in enumerate(units):
        if candidate.pk == current_unit.pk:
            current_index = index
            break
    if current_index is None:
        return None
    index = current_index + step
    while 0 <= index < len(units):
        if unit_visible_in_course_view(units[index], self_paced_view):
            return units[index]
        index += step
    return None


def get_next_incomplete_unit_for_view(course, user, self_paced_view):
    units = course_units_for_view(get_all_units_ordered(course), self_paced_view)
    if not units:
        return None
    completed_ids = set(UserCourseProgress.objects.filter(
        user=user, unit__in=units, completed_at__isnull=False,
    ).values_list('unit_id', flat=True))
    for unit in units:
        if unit.pk not in completed_ids:
            return unit
    return None


def completed_units_by_module(user, course, modules, self_paced_view):
    ordered = get_units_ordered_from_syllabus(modules)
    visible_ids = set()
    for unit in course_units_for_view(ordered, self_paced_view):
        visible_ids.add(unit.pk)
    completed_ids = set()
    completed_counts = Counter()
    rows = UserCourseProgress.objects.filter(
        user=user, unit__module__course=course, completed_at__isnull=False,
    ).values_list('unit_id', 'unit__module_id')
    for unit_id, module_id in rows:
        if unit_id in visible_ids:
            completed_ids.add(unit_id)
            completed_counts[module_id] += 1
    return completed_ids, dict(completed_counts)


def course_progress_counts(user, course, self_paced_view):
    units = required_course_units_for_view(course, self_paced_view)
    total = units.count()
    completed = UserCourseProgress.objects.filter(
        user=user, unit__in=units, completed_at__isnull=False,
    ).count()
    return CourseProgressCounts(completed=completed, total=total)


def reader_progress(course, unit, ordered_units, completed_ids, self_paced_view):
    required_ids = set(required_course_units_for_view(
        course, self_paced_view,
    ).values_list('pk', flat=True))
    progress_ids = []
    visible_units = course_units_for_view(ordered_units, self_paced_view)
    for visible_unit in visible_units:
        if visible_unit.pk in required_ids:
            progress_ids.append(visible_unit.pk)
    current = 1
    if unit.pk in progress_ids:
        current = progress_ids.index(unit.pk) + 1
    return {
        'current': current,
        'total': len(progress_ids),
        'completed': len(completed_ids & required_ids),
    }
