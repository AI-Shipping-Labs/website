"""Course Home's Current module card and Next live session block.

Home presents one module at a time: the running cohort's scheduled module, or,
without a calendar, the module holding the learner's first unfinished core
lesson (labelled "Your next module", since there is no cohort week). The card
owns that module's progress, one primary action, its homework and project
deliverables, and its own live sessions. A dated cohort's next upcoming
session gets its own block, and "Due next" lists the nearest open
deliverables from any module.
"""

from __future__ import annotations

import datetime
from zoneinfo import ZoneInfo

from community_base.homework_steps.models import HomeworkDraft

from content.models.course import UNIT_KIND_EVENT, UNIT_KIND_HOMEWORK, UNIT_KIND_LESSON, Unit
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
UNSCHEDULED_SESSION_STATUS = 'Not scheduled'


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


def homework_state(unit, user, cohort):
    """The learner's state on one homework unit.

    Returns ``questions`` (authored question count), ``answered`` (questions
    with a saved draft answer; all of them once submitted), ``submitted``,
    and ``has_draft``.
    """
    homework = resolve_homework_for_unit(unit, user, cohort=cohort)
    questions = list(homework.questions.all()) if homework else []
    submitted = bool(homework) and Submission.objects.filter(
        student=user, homework=homework,
    ).exists()
    answered = 0
    if submitted:
        answered = len(questions)
    elif homework is not None:
        draft = HomeworkDraft.objects.filter(
            user=user, assignment_key=f'aisl:homework:{homework.pk}',
        ).only('answers').first()
        answers = draft.answers if draft and isinstance(draft.answers, dict) else {}
        for question in questions:
            if _has_answer(answers.get(question_key(question))):
                answered += 1
    return {
        'questions': len(questions),
        'answered': answered,
        'submitted': submitted,
        'has_draft': answered > 0 and not submitted,
    }


def _homework_item_counts(unit, user, cohort, completed_ids):
    """Return ``(done, total)`` for one homework unit, one item per question.

    A submitted homework counts every question as done. Before submitting,
    each question with a saved draft answer counts. A homework with no
    authored questions is one item, done once submitted or its unit is complete.
    """
    state = homework_state(unit, user, cohort)
    if not state['questions']:
        return (1 if state['submitted'] or unit.pk in completed_ids else 0), 1
    return state['answered'], state['questions']


def _question_label(state):
    """``5 questions · 2 answered`` for a homework with authored questions."""
    total = state['questions']
    if not total:
        return ''
    label = f'{total} question{"s" if total != 1 else ""}'
    if state['answered'] and not state['submitted']:
        label += f' · {state["answered"]} answered'
    return label


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


def deliverable_key(row):
    """Identity of one homework or project deliverable across Home sections."""
    if row['kind'] == 'Project':
        return ('Project', row.get('project_id') or row['title'])
    return ('Homework', row.get('unit_content_id') or row['title'])


def _display_row(row, *, unit, user, cohort, cohort_query):
    """Shape one deliverable for Home: status always shown, question counts.

    An open homework nobody has started reads "Not started" rather than
    "Not submitted".
    """
    row = dict(row)
    if cohort_query and row['url'].startswith('/courses/') and '?' not in row['url']:
        row['url'] += cohort_query
    row['question_label'] = ''
    if row['kind'] == 'Homework' and unit is not None:
        state = homework_state(unit, user, cohort)
        row['question_label'] = _question_label(state)
        if row['status'] == 'Not submitted' and not row['closed'] and not state['has_draft']:
            row['status'] = 'Not started'
            row['status_tone'] = 'neutral'
    return row


def _homework_units_by_content_id(course, rows):
    content_ids = [row['unit_content_id'] for row in rows if row.get('unit_content_id')]
    if not content_ids:
        return {}
    return {
        str(unit.content_id): unit
        for unit in Unit.objects.filter(
            module__course=course, kind=UNIT_KIND_HOMEWORK, content_id__in=content_ids,
        ).select_related('module__course')
    }


def _sort_deliverables(rows):
    rows.sort(key=lambda row: (
        row['kind'] != 'Homework', row['when'] is None,
        row['when'].timestamp() if row['when'] else 0,
    ))
    return rows


def _deliverable_rows(commitments, module, *, user, cohort, cohort_query):
    """The module's own homework and projects for a learner with a cohort."""
    source = [
        row for row in commitments['open_assignments'] + commitments['completed_assignments']
        if row['kind'] in DELIVERABLE_KINDS and row.get('module_id') == module.pk
    ]
    units = _homework_units_by_content_id(module.course, source)
    rows = []
    for row in source:
        row = _display_row(
            row, unit=units.get(row.get('unit_content_id') or ''),
            user=user, cohort=cohort, cohort_query=cohort_query,
        )
        # The card already names the module.
        row['module_title'] = ''
        rows.append(row)
    return _sort_deliverables(rows)


def _undated_deliverable_rows(work_items, *, user, cohort):
    """Deliverable rows for a learner without a cohort: no dates, same anatomy."""
    rows = []
    for item in work_items:
        unit = item['unit']
        state = homework_state(unit, user, cohort)
        if state['submitted']:
            status, tone, verb = 'Submitted', 'success', 'View'
        elif state['has_draft']:
            status, tone, verb = 'Draft saved', 'info', 'Continue'
        else:
            status, tone, verb = 'Not started', 'neutral', 'Start'
        rows.append({
            'kind': 'Homework',
            'title': unit.title,
            'module_title': '',
            'when': None,
            'status': status,
            'status_tone': tone,
            'question_label': _question_label(state),
            'url': item['url'],
            'action': f'{verb} homework' if item['url'] else '',
            'detail': '',
            'complete': state['submitted'],
            'closed': False,
            'unit_content_id': item['content_id'],
        })
    return rows


def _primary_action(module, next_module, user, cohort, completed_ids, *, today,
                    deliverables, cohort_query):
    """Continue lesson, then the first open deliverable, then the next module.

    "Continue lesson" only ever targets a core ``kind='lesson'`` unit, never
    a session or homework unit.
    """
    lesson = _first_open_lesson(module, user, cohort, completed_ids, today)
    if lesson is not None:
        return {'label': 'Continue lesson', 'url': lesson.get_absolute_url() + cohort_query}
    for row in deliverables:
        if not row['complete'] and row['url'] and row['action']:
            return {'label': row['action'], 'url': row['url']}
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
    """The one action a course session row offers: open its syllabus unit.

    The session unit page carries the recording embed, the recap, and the
    join button, so course surfaces never link out to the event page's
    recording or recap.  Only a session without a unit falls back to the
    event page.
    """
    url = _session_url(row, cohort_query)
    if not url:
        return []
    label = 'Join session' if row['status'] == 'Live now' else 'Open session'
    return [{'label': label, 'url': url}]


def _session_contents(row):
    """What a past session's unit holds: ``Recording · Recap`` or neither."""
    if row['status'] != 'Past':
        return ''
    event = row.get('event')
    parts = []
    if event is not None and event.has_recording:
        parts.append('Recording')
    if row.get('recap_url'):
        parts.append('Recap')
    return ' · '.join(parts) or 'No recap yet'


def session_item(row, cohort_query):
    item = dict(row)
    unit = row.get('session_unit')
    item['display_title'] = unit.title if unit is not None else row['title']
    if row['status'] == UNSCHEDULED_SESSION_STATUS:
        # Nothing resolved for any cohort: say so, and offer no link that
        # would only lead to the same empty state.
        item['status'] = ''
        item['date_to_be_announced'] = True
        item['actions'] = []
        item['contents_label'] = ''
        return item
    item['date_to_be_announced'] = False
    item['actions'] = _session_actions(row, cohort_query)
    item['contents_label'] = _session_contents(row)
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


def _next_session(schedule, cohort):
    """The dated cohort's nearest upcoming session, from any module."""
    if cohort is None or cohort.mode != 'cohort':
        return None
    upcoming = [
        row for row in schedule
        if row['status'] in UPCOMING_STATUSES and row['when'] is not None
    ]
    if not upcoming:
        return None
    return min(upcoming, key=lambda row: row['when'])


def _week_start(day):
    return day - datetime.timedelta(days=day.weekday())


def build_due_next(course, user, cohort, commitments, *, cohort_query, now):
    """Home's "Due next": the nearest open homework and project deliverables.

    Open (not submitted, not closed) deliverables of a dated cohort, from any
    module. This week (Monday to Sunday in the display timezone) includes
    still-open overdue items. When nothing is due this week, the week of the
    earliest upcoming due date is shown instead. No cohort means no dates, so
    there is nothing to show.
    """
    empty = {'due_next': None}
    if cohort is None or cohort.mode != 'cohort':
        return empty
    candidates = [
        row for row in commitments['open_assignments']
        if row['kind'] in DELIVERABLE_KINDS and row['when'] is not None
        and not row['complete'] and not row['closed']
    ]
    if not candidates:
        return empty
    zone = ZoneInfo(commitments['commitment_timezone'])
    this_week = _week_start(now.astimezone(zone).date())

    def week_of(row):
        return _week_start(row['when'].astimezone(zone).date())

    chosen = [row for row in candidates if week_of(row) <= this_week]
    week = this_week
    if not chosen:
        week = min(week_of(row) for row in candidates)
        chosen = [row for row in candidates if week_of(row) == week]
    units = _homework_units_by_content_id(course, chosen)
    rows = []
    for row in chosen:
        display = _display_row(
            row, unit=units.get(row.get('unit_content_id') or ''),
            user=user, cohort=cohort, cohort_query=cohort_query,
        )
        if display['kind'] == 'Project':
            display['module_title'] = row.get('project_group_title') or ''
        rows.append(display)
    # Homework before projects, each by due date.
    rows.sort(key=lambda row: (row['kind'] != 'Homework', row['when']))
    if week == this_week:
        title = 'Due this week'
    else:
        title = f'Due next · week of {week:%b} {week.day}'
    return {'due_next': {
        'title': title,
        'rows': rows,
        'keys': {deliverable_key(row) for row in rows},
    }}


def build_current_module(course, user, cohort, home, commitments, *, completed_ids,
                         cohort_query, today, due_next_keys=frozenset()):
    """Template context for the Current module card and Next live session block.

    ``due_next_keys`` are deliverables already listed in "Due next"; the card
    does not repeat them.
    """
    module = home['current_module']
    if module is None:
        return {'current_module': None, 'home_next_session': None}
    next_module = next_core_module(home['ordered_modules'], module)
    week_dates = home['week_dates']
    week_label, week_range = _week_label(module, week_dates, cohort)

    if cohort:
        deliverables = _deliverable_rows(
            commitments, module, user=user, cohort=cohort, cohort_query=cohort_query,
        )
    else:
        deliverables = _undated_deliverable_rows(
            commitments['focus_work_items'], user=user, cohort=cohort,
        )
    # "Due next" already lists some of these; the card shows the rest, but
    # the primary action still considers every deliverable of the module.
    shown_deliverables = [
        row for row in deliverables if deliverable_key(row) not in due_next_keys
    ]
    session_rows = [
        row for row in commitments['live_session_schedule']
        if row.get('session_unit') is not None
        and _top_module_id(row['session_unit'].module) == module.pk
    ]
    in_cohort_week = bool(
        week_label and home.get('current_cohort_module') is not None
        and home['current_cohort_module'].pk == module.pk
    )

    next_row = _next_session(commitments['live_session_schedule'], cohort)
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
            'eyebrow': 'Current module' if in_cohort_week else 'Your next module',
            'week_label': week_label,
            'week_range': week_range,
            'progress': module_progress(module, user, cohort, completed_ids),
            'action': _primary_action(
                module, next_module, user, cohort, completed_ids, today=today,
                deliverables=deliverables, cohort_query=cohort_query,
            ),
            'deliverables': shown_deliverables,
            'sessions': [session_item(row, cohort_query) for row in session_rows],
        },
        'home_next_session': next_live_session,
    }
