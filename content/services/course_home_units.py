"""Course Home unit rows and viewer-specific curriculum shaping."""

from dataclasses import dataclass

from content.models.course import UNIT_KIND_EVENT, UNIT_KIND_HOMEWORK, UNIT_KIND_LESSON
from content.services.course_units import format_week_range
from content.services.course_view_units import course_units_for_view

MATERIAL_KINDS = {UNIT_KIND_LESSON, UNIT_KIND_EVENT}


@dataclass(frozen=True)
class CourseHomeUnitRows:
    rows: list
    optional_child_rows: list
    required_units: list


def _required_first(items):
    required = []
    optional = []
    for item in items:
        if item.is_bonus:
            optional.append(item)
        else:
            required.append(item)
    return required + optional


def all_module_units(module, self_paced_view=False):
    """Return one module's units in curriculum and viewer order."""
    children = list(module.children.all())
    if not children:
        return list(course_units_for_view(module.units.all(), self_paced_view))
    units = []
    for child in _required_first(children):
        units.extend(child.units.all())
    return list(course_units_for_view(units, self_paced_view))


def material_units(module, self_paced_view):
    units = []
    for unit in all_module_units(module, self_paced_view):
        if unit.kind in MATERIAL_KINDS:
            units.append(unit)
    return units


def first_instructional_module(modules, orientation_ids, self_paced_view):
    instructional_kinds = MATERIAL_KINDS | {UNIT_KIND_HOMEWORK}
    for module in modules:
        if module.is_bonus or module.pk in orientation_ids:
            continue
        for unit in all_module_units(module, self_paced_view):
            if unit.kind in instructional_kinds and not unit.effective_is_bonus:
                return module
    return None


def focus_homework_units(module, self_paced_view):
    if module is None:
        return []
    units = []
    for unit in all_module_units(module, self_paced_view):
        if unit.kind == UNIT_KIND_HOMEWORK and not unit.effective_is_bonus:
            units.append(unit)
    return units


def _core_units(units):
    core = []
    for unit in units:
        if not unit.effective_is_bonus:
            core.append(unit)
    return core


def _optional_rows(course, module, completed_ids, week, self_paced_view):
    rows = []
    if module.is_bonus:
        return rows
    week_range = ''
    if week:
        week_range = format_week_range(*week)
    for child in module.children.all():
        if not child.is_bonus:
            continue
        units = []
        visible_units = course_units_for_view(child.units.all(), self_paced_view)
        for unit in visible_units:
            if unit.kind in MATERIAL_KINDS:
                units.append(unit)
        unit_ids = set()
        for unit in units:
            unit_ids.add(unit.pk)
        rows.append({
            'module': child,
            'url': f'/courses/{course.slug}/{module.slug}/{child.slug}',
            'total': len(units),
            'completed': len(completed_ids & unit_ids),
            'optional': True,
            'capstone': False,
            'week_range': week_range,
            'next_available': None,
        })
    return rows


def build_course_home_unit_rows(
    course, modules, completed_ids, capstone_ids, week_dates, today,
    self_paced_view, available_on,
):
    rows = []
    optional_rows = []
    required_units = []
    for module in modules:
        units = material_units(module, self_paced_view)
        core_units = _core_units(units)
        required_units.extend(core_units)
        week = week_dates.get(module.pk)
        row = _build_row(
            course, module, core_units, completed_ids, capstone_ids,
            week, today, available_on,
        )
        rows.append(row)
        optional_rows.extend(_optional_rows(
            course, module, completed_ids, week, self_paced_view,
        ))
    return CourseHomeUnitRows(rows, optional_rows, required_units)


def _build_row(
    course, module, core_units, completed_ids, capstone_ids, week, today,
    available_on,
):
    future_dates = []
    unit_ids = set()
    for unit in core_units:
        unit_ids.add(unit.pk)
        available_date = available_on(unit)
        if available_date and available_date > today:
            future_dates.append(available_date)
    week_range = ''
    if week:
        week_range = format_week_range(*week)
    next_available = None
    if future_dates:
        next_available = min(future_dates)
    return {
        'module': module, 'url': f'/courses/{course.slug}/{module.slug}',
        'total': len(core_units), 'completed': len(completed_ids & unit_ids),
        'optional': module.is_bonus, 'capstone': module.pk in capstone_ids,
        'week_range': week_range, 'next_available': next_available,
    }
