"""Course Home's Current module card and Next live session block.

Home presents one module at a time: the running cohort's scheduled module, or,
without a calendar, the module holding the learner's first unfinished core
lesson. The card owns that module's progress, one primary action, its
homework and project deliverables, and its own live sessions. The cohort's
next upcoming session gets a separate block only when it belongs elsewhere.
"""

from __future__ import annotations

from community_base.homework_steps.models import HomeworkDraft

from content.models.course import UNIT_KIND_EVENT, UNIT_KIND_HOMEWORK, UNIT_KIND_LESSON
from content.models.homework import Submission
from content.models.peer_review import ProjectSubmission
from content.services.course_home import _all_module_units, next_core_module
from content.services.course_schedule import cohort_projects
from content.services.course_units import (
    decide_course_unit_access,
    decide_course_unit_drip_lock,
    format_week_range,
    resolve_homework_for_unit,
)
from content.services.homework_step_reader import question_key

UPCOMING_STATUSES = ('Live now', 'In progress', 'Upcoming')
DELIVERABLE_KINDS = ('Homework', 'Project')


def _top_module_id(module):
    return module.parent_id or module.pk


def _counted_units(module):
    """Core units that count as one item each: not optional, not a session or homework."""
    units = []
    for unit in _all_module_units(module):
        if unit.effective_is_bonus:
            continue
        if unit.kind in (UNIT_KIND_EVENT, UNIT_KIND_HOMEWORK):
            continue
        units.append(unit)
    return units


def _homework_units(module):
    units = []
    for unit in _all_module_units(module):
        if unit.kind == UNIT_KIND_HOMEWORK and not unit.effective_is_bonus:
            units.append(unit)
    return units


def _has_answer(value):
    return value not in (None, '', [], ())


def _homework_item_counts(unit, user, cohort, completed_ids):
    """Return ``(done, total)`` for one homework unit, one item per question.

    A submitted homework counts every question as done. Before submitting,
    each question with a saved draft answer counts. A homework with no
    authored questions is one item, done once its unit is complete.
    """
    homework = resolve_homework_for_unit(unit, user, cohort=cohort)
    questions = list(homework.questions.all()) if homework else []
    if not questions:
        submitted = bool(homework) and Submission.objects.filter(
            student=user, homework=homework,
        ).exists()
        return (1 if submitted or unit.pk in completed_ids else 0), 1
    total = len(questions)
    if Submission.objects.filter(student=user, homework=homework).exists():
        return total, total
    draft = HomeworkDraft.objects.filter(
        user=user, assignment_key=f'aisl:homework:{homework.pk}',
    ).only('answers').first()
    answers = draft.answers if draft and isinstance(draft.answers, dict) else {}
    done = 0
    for question in questions:
        if _has_answer(answers.get(question_key(question))):
            done += 1
    return done, total


def module_progress(module, user, cohort, completed_ids):
    """Count everything in ``module`` the learner is expected to finish.

    Items are each core unit, each question of the module's homework, and
    one per project deliverable (alternative attempts are one deliverable).
    Optional units and live sessions do not count.
    """
    lessons = _counted_units(module)
    lessons_done = 0
    for unit in lessons:
        if unit.pk in completed_ids:
            lessons_done += 1

    questions_done = 0
    questions_total = 0
    for unit in _homework_units(module):
        done, total = _homework_item_counts(unit, user, cohort, completed_ids)
        questions_done += done
        questions_total += total

    projects = [
        project for project in cohort_projects(module.course, cohort).select_related('module')
        if project.module_id and _top_module_id(project.module) == module.pk
    ]
    projects_total = 1 if projects else 0
    projects_done = 0
    if projects and ProjectSubmission.objects.filter(
        user=user, course_project__in=projects,
    ).exists():
        projects_done = 1

    done = lessons_done + questions_done + projects_done
    total = len(lessons) + questions_total + projects_total
    return {
        'done': done,
        'total': total,
        'percent': int(done / total * 100) if total else 0,
        'lessons_done': lessons_done,
        'lessons_total': len(lessons),
        'questions_done': questions_done,
        'questions_total': questions_total,
        'projects_done': projects_done,
        'projects_total': projects_total,
    }


def _first_open_lesson(module, user, cohort, completed_ids, today):
    for unit in _all_module_units(module):
        if unit.kind != UNIT_KIND_LESSON or unit.effective_is_bonus:
            continue
        if unit.pk in completed_ids:
            continue
        if not decide_course_unit_access(user, unit).has_access:
            continue
        if decide_course_unit_drip_lock(user, unit, today=today, cohort=cohort).is_locked:
            continue
        return unit
    return None


def _first_lesson(module):
    for unit in _all_module_units(module):
        if unit.kind == UNIT_KIND_LESSON and not unit.effective_is_bonus:
            return unit
    return None


def _module_url(course, module, cohort_query):
    return f'/courses/{course.slug}/{module.slug}{cohort_query}'


def _deliverable_rows(commitments, module, cohort_query):
    rows = []
    for row in commitments['open_assignments'] + commitments['completed_assignments']:
        if row['kind'] not in DELIVERABLE_KINDS or row.get('module_id') != module.pk:
            continue
        row = dict(row)
        # The card already names the module.
        row['module_title'] = ''
        if cohort_query and row['url'].startswith('/courses/') and '?' not in row['url']:
            row['url'] += cohort_query
        rows.append(row)
    rows.sort(key=lambda row: (
        row['kind'] != 'Homework', row['when'] is None,
        row['when'].timestamp() if row['when'] else 0,
    ))
    return rows


def _primary_action(module, next_module, user, cohort, completed_ids, *, today,
                    deliverables, undated_work, cohort_query):
    """Continue lesson, then the first open deliverable, then the next module."""
    lesson = _first_open_lesson(module, user, cohort, completed_ids, today)
    if lesson is not None:
        return {'label': 'Continue lesson', 'url': lesson.get_absolute_url() + cohort_query}
    for row in deliverables:
        if not row['complete'] and row['url'] and row['action']:
            return {'label': row['action'], 'url': row['url']}
    for item in undated_work:
        if item['url'] and item['action'] and not (
            item['commitment'] and item['commitment']['complete']
        ):
            return {'label': item['action'], 'url': item['url']}
    if next_module is not None:
        first = _first_lesson(next_module)
        url = (
            first.get_absolute_url() + cohort_query if first
            else _module_url(module.course, next_module, cohort_query)
        )
        return {'label': f'Next module: {next_module.title}', 'url': url}
    return {'label': 'Review module', 'url': _module_url(module.course, module, cohort_query)}


def _session_url(row, cohort_query):
    unit = row.get('session_unit')
    if unit is not None:
        return unit.get_absolute_url() + cohort_query
    return row.get('event_url') or ''


def _session_actions(row, cohort_query):
    """The action links one session row offers for its current stage."""
    if row['status'] == 'Past':
        actions = []
        event = row.get('event')
        if event is not None and event.has_recording:
            actions.append({'label': 'Watch recording', 'url': event.get_recording_url()})
        if row['recap_url']:
            actions.append({'label': 'Read recap', 'url': row['recap_url']})
        return actions
    if row['status'] == 'Live now' and row['url']:
        return [{'label': 'Join session', 'url': row['url']}]
    url = _session_url(row, cohort_query)
    return [{'label': 'Open session', 'url': url}] if url else []


def session_item(row, cohort_query):
    item = dict(row)
    unit = row.get('session_unit')
    item['display_title'] = unit.title if unit is not None else row['title']
    item['actions'] = _session_actions(row, cohort_query)
    return item


def _week_label(module, week_dates, cohort):
    """``Week N`` for a dated cohort's module, counted from the cohort start."""
    if cohort is None or cohort.mode != 'cohort' or cohort.start_date is None:
        return '', ''
    if module.pk not in week_dates:
        return '', ''
    start, end = week_dates[module.pk]
    number = 1 + (start - cohort.start_date).days // 7
    return f'Week {number}', format_week_range(start, end)


def _next_session(schedule, module_session_rows):
    upcoming = [
        row for row in schedule
        if row['status'] in UPCOMING_STATUSES and row['when'] is not None
    ]
    if not upcoming:
        return None
    nearest = min(upcoming, key=lambda row: row['when'])
    for row in module_session_rows:
        if row is nearest:
            return None
    return nearest


def build_current_module(course, user, cohort, home, commitments, *, completed_ids,
                         cohort_query, today):
    """Template context for the Current module card and Next live session block."""
    module = home['current_module']
    if module is None:
        return {'current_module': None, 'home_next_session': None}
    next_module = next_core_module(home['ordered_modules'], module)
    week_dates = home['week_dates']
    week_label, week_range = _week_label(module, week_dates, cohort)

    deliverables = _deliverable_rows(commitments, module, cohort_query)
    undated_work = [] if cohort else commitments['focus_work_items']
    session_rows = [
        row for row in commitments['live_session_schedule']
        if row.get('session_unit') is not None
        and _top_module_id(row['session_unit'].module) == module.pk
    ]

    next_row = _next_session(commitments['live_session_schedule'], session_rows)
    next_live_session = None
    if next_row is not None:
        next_live_session = session_item(next_row, cohort_query)
        session_unit = next_row.get('session_unit')
        module_label = ''
        if session_unit is not None:
            session_module = session_unit.module.parent or session_unit.module
            if session_module.pk != module.pk:
                label, _ = _week_label(session_module, week_dates, cohort)
                module_label = f'{label} · {session_module.title}' if label else session_module.title
        next_live_session['module_label'] = module_label

    return {
        'current_module': {
            'module': module,
            'url': _module_url(course, module, cohort_query),
            'week_label': week_label,
            'week_range': week_range,
            'progress': module_progress(module, user, cohort, completed_ids),
            'action': _primary_action(
                module, next_module, user, cohort, completed_ids, today=today,
                deliverables=deliverables, undated_work=undated_work,
                cohort_query=cohort_query,
            ),
            'deliverables': deliverables,
            'undated_work': undated_work,
            'sessions': [session_item(row, cohort_query) for row in session_rows],
        },
        'home_next_session': next_live_session,
    }
