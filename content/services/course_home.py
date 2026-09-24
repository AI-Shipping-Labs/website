"""Learner course-home presentation from the current curriculum and progress rows."""

from __future__ import annotations

import datetime
import re
from urllib.parse import urlencode

from django.utils import timezone

from content.access import LEVEL_BASIC, LEVEL_OPEN, LEVEL_REGISTERED, get_user_level
from content.models import CourseAccess, UserCourseProgress
from content.models.course import UNIT_KIND_EVENT, UNIT_KIND_HOMEWORK, UNIT_KIND_LESSON
from content.models.peer_review import CourseProject
from content.services.course_units import (
    build_module_week_dates,
    decide_course_unit_access,
    decide_course_unit_drip_lock,
    format_week_range,
)

MATERIAL_KINDS = {UNIT_KIND_LESSON, UNIT_KIND_EVENT}
# Source-authored curriculum identities. Labels and destinations are read from
# synced Unit rows, so no help text or invitation is copied into application code.
HELP_UNIT_SLUGS = ('communication', 'office-hours')
ORIENTATION_WORDS = {'orientation', 'logistics', 'welcome', 'introduction'}


def _is_orientation_module(module):
    """Use authored first-module wording; Module has no orientation role field."""
    return bool(ORIENTATION_WORDS & set(re.findall(r'[a-z]+', module.title.lower())))


def _unit_available_on(unit, cohort):
    offset = unit.effective_available_after_days
    if cohort is None or cohort.start_date is None or offset is None:
        return None
    return cohort.start_date + datetime.timedelta(days=offset)


def _ordered_modules(course):
    """Use the same prefetched tree and required-before-optional order as the reader."""
    top = list(course.get_syllabus())
    return [module for module in top if not module.is_bonus] + [
        module for module in top if module.is_bonus
    ]


def _material_units(module):
    children = list(module.children.all())
    if children:
        ordered = [child for child in children if not child.is_bonus] + [
            child for child in children if child.is_bonus
        ]
        return [unit for child in ordered for unit in child.units.all()
                if unit.kind in MATERIAL_KINDS]
    return [unit for unit in module.units.all() if unit.kind in MATERIAL_KINDS]


def _all_module_units(module):
    """Return a module's units in curriculum order, including submodule units."""
    children = list(module.children.all())
    if not children:
        return list(module.units.all())
    ordered = [child for child in children if not child.is_bonus] + [
        child for child in children if child.is_bonus
    ]
    return [unit for child in ordered for unit in child.units.all()]


def _current_cohort_module(modules, week_dates, cohort, today):
    """Pick the scheduled top-level module for the selected cohort date."""
    if cohort is None or cohort.start_date is None:
        return None
    dated = [
        module for module in modules
        if not module.is_bonus and module.pk in week_dates
    ]
    if not dated:
        return None
    for module in dated:
        start, end = week_dates[module.pk]
        if start <= today <= end:
            return module
    earlier = [module for module in dated if week_dates[module.pk][1] < today]
    if earlier:
        return earlier[-1]
    return dated[0]


def _top_level_module(module):
    if module is None:
        return None
    return module.parent if module.parent_id else module


def _can_open_unit(unit, *, user_level, individual_access, entitlement_mode, is_staff, verified):
    """Bulk policy screen; the eventual recommendation is checked by the reader policy."""
    if unit.is_preview:
        return True
    required = unit.effective_required_level
    if required == LEVEL_OPEN:
        return True
    if required == LEVEL_REGISTERED:
        return user_level >= LEVEL_BASIC or verified
    if individual_access:
        return True
    if entitlement_mode:
        return is_staff
    return user_level >= required


def build_course_home(course, user, cohort, *, today=None):
    """Return one coherent view model; database query count is independent of module count."""
    today = today or timezone.localdate()
    modules = _ordered_modules(course)
    completed_ids = set(UserCourseProgress.objects.filter(
        user=user, unit__module__course=course, completed_at__isnull=False,
    ).values_list('unit_id', flat=True))
    capstone_ids = {
        parent_id or module_id
        for module_id, parent_id in CourseProject.objects.filter(
            course=course, module__isnull=False,
        ).values_list('module_id', 'module__parent_id')
    }
    user_level = get_user_level(user)
    individual_access = CourseAccess.objects.filter(user=user, course=course).exists()
    week_dates = build_module_week_dates(
        modules, cohort, extend_final_to_cohort_end=course.slug == 'ai-buildcamp',
    )
    rows = []
    optional_child_rows = []
    all_units = []
    required_units = []
    for module in modules:
        units = _material_units(module)
        all_units.extend(units)
        core_units = [unit for unit in units if not unit.effective_is_bonus]
        required_units.extend(core_units)
        week = week_dates.get(module.pk)
        available_dates = [_unit_available_on(unit, cohort) for unit in core_units]
        future_dates = [date for date in available_dates if date and date > today]
        rows.append({
            'module': module,
            'url': f'/courses/{course.slug}/{module.slug}',
            'total': len(core_units),
            'completed': len(completed_ids & {unit.pk for unit in core_units}),
            'optional': module.is_bonus,
            'capstone': module.pk in capstone_ids,
            'week_range': format_week_range(*week) if week else '',
            'next_available': min(future_dates) if future_dates else None,
        })
        if not module.is_bonus:
            for child in module.children.all():
                if not child.is_bonus:
                    continue
                # The curriculum caps nesting at two module levels; the
                # prefetched leaf units need no child lookup per optional row.
                child_units = [unit for unit in child.units.all()
                               if unit.kind in MATERIAL_KINDS]
                optional_child_rows.append({
                    'module': child,
                    'url': f'/courses/{course.slug}/{module.slug}/{child.slug}',
                    'total': len(child_units),
                    'completed': len(completed_ids & {unit.pk for unit in child_units}),
                    'optional': True,
                    'capstone': False,
                    'week_range': format_week_range(*week) if week else '',
                    'next_available': None,
                })

    orientation_rows = [
        row for row in rows[:1]
        if not row['optional'] and not row['capstone']
        and _is_orientation_module(row['module'])
    ]
    orientation_module_ids = {row['module'].pk for row in orientation_rows}
    unscheduled_cohort = cohort is None or cohort.mode == 'self_paced'

    recommendation = None
    locked_dates = []
    # Session units can be schedule-only stubs. Keep them in core progress, but
    # lead with an actual lesson while one is available anywhere in the syllabus.
    recommendation_order = [unit for unit in required_units if unit.kind == UNIT_KIND_LESSON]
    recommendation_order += [unit for unit in required_units if unit.kind == UNIT_KIND_EVENT]
    recommendation_passes = (True, False) if unscheduled_cohort and orientation_module_ids else (False,)
    for skip_orientation in recommendation_passes:
        for unit in recommendation_order:
            if (
                skip_orientation
                and _top_level_module(unit.module).pk in orientation_module_ids
            ):
                continue
            if unit.pk in completed_ids:
                continue
            available_date = _unit_available_on(unit, cohort)
            if available_date and today < available_date:
                locked_dates.append(available_date)
                continue
            if not _can_open_unit(
                unit, user_level=user_level, individual_access=individual_access,
                entitlement_mode=course.access_mode == 'entitlement',
                is_staff=user.is_staff or user.is_superuser,
                verified=user.email_verified,
            ):
                continue
            # Reuse the reader's final decisions for the one lesson we recommend.
            if not decide_course_unit_access(user, unit).has_access:
                continue
            reader_drip = decide_course_unit_drip_lock(user, unit, today=today, cohort=cohort)
            if reader_drip.is_locked:
                if reader_drip.available_date:
                    locked_dates.append(reader_drip.available_date)
                continue
            recommendation = unit
            break
        if recommendation:
            break

    core_total = len(required_units)
    core_completed = len(completed_ids & {unit.pk for unit in required_units})
    if not core_total:
        action = 'empty'
    elif core_completed == core_total:
        action = 'complete'
    elif recommendation is None:
        action = 'locked'
    elif core_completed:
        action = 'continue'
    else:
        action = 'start'

    cohort_status = ''
    current_week = None
    if cohort and cohort.start_date and cohort.end_date:
        if today < cohort.start_date:
            cohort_status = 'upcoming'
        elif today > cohort.end_date:
            cohort_status = 'completed'
        else:
            cohort_status = 'in progress'
            current_week = 1 + (today - cohort.start_date).days // 7

    current_cohort_module = _current_cohort_module(modules, week_dates, cohort, today)
    first_instructional_module = next((
        module for module in modules
        if not module.is_bonus and module.pk not in orientation_module_ids
        and any(
            unit.kind in MATERIAL_KINDS | {UNIT_KIND_HOMEWORK}
            and not unit.effective_is_bonus
            for unit in _all_module_units(module)
        )
    ), None)
    recommended_module = _top_level_module(recommendation.module if recommendation else None)
    focus_module = current_cohort_module or recommended_module
    if unscheduled_cohort and (
        focus_module is None or focus_module.pk in orientation_module_ids
    ):
        focus_module = first_instructional_module or focus_module
    focus_week_range = ''
    if focus_module and focus_module.pk in week_dates:
        focus_week_range = format_week_range(*week_dates[focus_module.pk])
    focus_module_row = next(
        (row for row in rows if row['module'].pk == focus_module.pk), None,
    ) if focus_module else None
    focus_work_units = [
        unit for unit in _all_module_units(focus_module)
        if unit.kind == 'homework' and not unit.effective_is_bonus
    ] if focus_module else []

    help_links = []
    help_units = {unit.slug: unit for unit in all_units if unit.slug in HELP_UNIT_SLUGS}
    cohort_suffix = (
        f'?{urlencode({"cohort": cohort.external_key})}'
        if cohort and cohort.external_key and cohort.mode == 'cohort' else ''
    )
    if course.discussion_url:
        help_links.append(('Course communication', course.discussion_url))
    elif 'communication' in help_units:
        unit = help_units['communication']
        help_links.append((unit.title, unit.get_absolute_url() + cohort_suffix))
    if cohort and cohort.event_series_id:
        help_links.append((cohort.event_series.name, cohort.event_series.get_absolute_url()))
    elif 'office-hours' in help_units:
        unit = help_units['office-hours']
        help_links.append((unit.title, unit.get_absolute_url() + cohort_suffix))
    if course.faq_url:
        help_links.append(('Frequently asked questions', course.faq_url))
    if course.docs_url:
        help_links.append(('Course documentation', course.docs_url))

    return {
        'course': course,
        'cohort': cohort,
        'cohort_status': cohort_status,
        'current_week': current_week,
        'current_cohort_module': current_cohort_module,
        'focus_module': focus_module,
        'focus_module_row': focus_module_row,
        'focus_week_range': focus_week_range,
        'focus_work_units': focus_work_units,
        'core_total': core_total,
        'core_completed': core_completed,
        'action': action,
        'recommended_unit': recommendation,
        'recommendation_label': (
            'Open session' if recommendation and recommendation.kind == UNIT_KIND_EVENT
            else 'Open lesson'
        ),
        'next_available': min(locked_dates) if locked_dates else None,
        'orientation_rows': orientation_rows,
        'core_rows': [
            row for row in rows
            if not row['optional'] and not row['capstone'] and row not in orientation_rows
        ],
        'capstone_rows': [row for row in rows if not row['optional'] and row['capstone']],
        'optional_rows': [row for row in rows if row['optional']] + optional_child_rows,
        'help_links': help_links,
    }
