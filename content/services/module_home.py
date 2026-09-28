"""Module home: the module page inside the course reader shell.

A module page shows the module's progress, one next-lesson action, and this
module's live sessions and homework/project work. Sessions and work come from
the same cohort-aware commitments as course Home
(``content.services.course_commitments``), so both pages agree on dates and
status. The view keeps access, projects, and the gated CTA; this module owns
the module-home shaping and the reader navigation around a module page.
"""

from urllib.parse import urlencode

from django.utils import timezone

from content.models import CohortEnrollment, UserCourseProgress
from content.services.course_commitments import build_course_commitments

WORK_KINDS = ('Homework', 'Project')


def _root_module_id(module):
    if module.parent_id:
        return module.parent_id
    return module.pk


def cohort_query_for(cohort_param):
    """Query string that keeps the selected cohort on module-home links."""
    if not cohort_param:
        return ''
    return f'?{urlencode({"cohort": cohort_param})}'


def completed_course_unit_ids(user, course):
    """Completed unit ids across the whole course.

    Course-wide, like the unit reader: the shared sidebar lists sibling
    submodules (and, for course-scoped navigation, every module), so a
    module-tree-only set would drop completion ticks outside this module.
    """
    if not user.is_authenticated:
        return set()
    return set(UserCourseProgress.objects.filter(
        user=user, unit__module__course=course, completed_at__isnull=False,
    ).values_list('unit_id', flat=True))


def module_reader_navigation(course, module, children):
    """Sidebar scope, adjacent modules, and the expanded section for a module page.

    Module pages share the course reader shell. Keep the relevant section
    expanded without treating one of its lessons as the current page.
    """
    modules = course.get_syllabus()
    navigation = {
        'modules': modules,
        'scoped_module': None,
        'previous_module': None,
        'next_module': None,
        'reader_active_module': module,
        'reader_active_submodule_id': _active_submodule_id(module, children),
    }
    if course.reader_navigation_scope not in ('module', 'submodule'):
        return navigation
    root_id = _root_module_id(module)
    for index, top_module in enumerate(modules):
        if top_module.pk != root_id:
            continue
        navigation['scoped_module'] = top_module
        if index > 0:
            navigation['previous_module'] = modules[index - 1]
        if index + 1 < len(modules):
            navigation['next_module'] = modules[index + 1]
        break
    return navigation


def _active_submodule_id(module, children):
    if module.parent_id:
        return module.pk
    if children:
        return children[0].pk
    return None


def module_tree_units(module, children):
    units = list(module.units.all())
    for child in children:
        units.extend(child.units.all())
    return units


def _core_units(tree_units):
    """Reading and homework material that counts toward module progress.

    Sessions have their own section, so progress and the next lesson count
    non-session, non-optional material only.
    """
    core = []
    for unit in tree_units:
        if unit.effective_is_bonus or unit.kind == 'event':
            continue
        core.append(unit)
    return core


def _action_units(tree_units, core_units):
    """Units the next-lesson action may open; an all-optional module still gets one."""
    if core_units:
        return core_units
    action_units = []
    for unit in tree_units:
        if unit.kind != 'event':
            action_units.append(unit)
    return action_units


def module_progress(core_units, completed_ids):
    total = len(core_units)
    completed = 0
    for unit in core_units:
        if unit.pk in completed_ids:
            completed += 1
    percent = 0
    if total:
        percent = int(completed / total * 100)
    return {
        'module_progress_total': total,
        'module_progress_completed': completed,
        'module_progress_pct': percent,
    }


def module_next_action(action_units, completed_ids, cohort_query, *, progress_completed):
    """The one next-lesson action: start, continue, or review a finished module."""
    if not action_units:
        return None
    label = 'Start module'
    if progress_completed:
        label = 'Continue'
    for unit in action_units:
        if unit.pk not in completed_ids:
            return _next_action(unit, cohort_query, label, complete=False)
    return _next_action(action_units[0], cohort_query, 'Review module', complete=True)


def _next_action(unit, cohort_query, label, *, complete):
    return {
        'unit': unit,
        'url': unit.get_absolute_url() + cohort_query,
        'label': label,
        'complete': complete,
    }


def commitment_cohort_for(course, user, viewer_cohort, viewer_is_preview, cohort_param):
    """The cohort whose dates the module home shows, or ``None`` for no deadlines.

    A learner without a real cohort (or an anonymous preview) gets no
    personal deadlines; staff may preview an explicitly selected cohort.
    """
    if viewer_cohort is not None and not viewer_is_preview:
        return viewer_cohort
    if user.is_staff and viewer_is_preview and cohort_param:
        return viewer_cohort
    if not user.is_authenticated or cohort_param:
        return None
    self_paced = CohortEnrollment.objects.filter(
        user=user, cohort__course=course,
        cohort__mode='self_paced', cohort__is_active=True,
    ).select_related('cohort').first()
    if self_paced is None:
        return None
    return self_paced.cohort


def _session_rows(commitments, root_id):
    rows = []
    for row in commitments.get('live_session_schedule', []):
        session_unit = row.get('session_unit')
        if session_unit is not None and _root_module_id(session_unit.module) == root_id:
            rows.append(row)
    return rows


def _is_visible_work_row(row, root_id, visible_project_ids):
    if row.get('module_id') != root_id or row['kind'] not in WORK_KINDS:
        return False
    # Projects follow the module page's cohort scoping, so an unscoped or
    # other-cohort attempt does not surface here.
    if row['kind'] == 'Project':
        return row.get('project_id') in visible_project_ids
    return True


def _work_sort_key(row):
    return (row['kind'] != 'Homework', row['when'] is None, row['when'] or timezone.now())


def _work_rows(commitments, root_id, visible_project_ids, cohort_query):
    candidates = commitments.get('open_assignments', []) + commitments.get('completed_assignments', [])
    rows = []
    for row in candidates:
        if _is_visible_work_row(row, root_id, visible_project_ids):
            rows.append(row)
    rows.sort(key=_work_sort_key)
    for row in rows:
        # The page already names the module; drop the per-row module label.
        row['module_title'] = ''
        # Project links are course-relative; keep the selected cohort on
        # them like every other module-home link.
        if cohort_query and row['url'].startswith('/courses/') and '?' not in row['url']:
            row['url'] += cohort_query
    return rows


def _rows_of_kind(rows, kind):
    matching = []
    for row in rows:
        if row['kind'] == kind:
            matching.append(row)
    return matching


def _any_dated(rows):
    for row in rows:
        if row.get('when'):
            return True
    return False


def _show_timezone_notice(session_rows, work_rows, cohort):
    if _any_dated(session_rows):
        return True
    return cohort is not None and cohort.mode == 'cohort' and _any_dated(work_rows)


def build_module_home(course, module, user, *, tree_units, completed_ids, has_access, cohort,
                      cohort_query, visible_project_ids):
    """Template context for the module home sections."""
    core_units = _core_units(tree_units)
    context = module_progress(core_units, completed_ids)
    context['module_next_action'] = None
    if has_access:
        action_units = _action_units(tree_units, core_units)
        context['module_next_action'] = module_next_action(
            action_units, completed_ids, cohort_query,
            progress_completed=context['module_progress_completed'],
        )
    commitments = {}
    if user.is_authenticated and has_access:
        commitments = build_course_commitments(course, user, cohort)
    root_id = _root_module_id(module)
    session_rows = _session_rows(commitments, root_id)
    work_rows = _work_rows(commitments, root_id, visible_project_ids, cohort_query)
    context.update({
        'module_session_rows': session_rows,
        'module_work_rows': work_rows,
        'module_homework_rows': _rows_of_kind(work_rows, 'Homework'),
        'module_project_rows': _rows_of_kind(work_rows, 'Project'),
        'cohort': cohort,
        'commitment_timezone': commitments.get('commitment_timezone', ''),
        'module_show_timezone_notice': _show_timezone_notice(session_rows, work_rows, cohort),
    })
    return context
