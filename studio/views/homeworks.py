"""Studio read-only views for homework and submissions -- issue #1683, tranche 1.

Distinguishes "an operator can see submissions exist and read them"
(required so the "never lost" promise is operationally verifiable by a
human during the live cohort) from "an operator can score, re-score, or
export" (the explicitly deferred operator surface -- not built here).
"""

import datetime

from django.db.models import Count
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from content.models import Course, Unit
from content.models.homework import Homework, HomeworkState, Submission
from studio.decorators import staff_required

# (studio_status_badge key, friendly label) per HomeworkState. Green is
# reserved for the live-positive "still accepting submissions" state;
# CLOSED and SCORED are both finished/past states, so they stay neutral
# (design-system: "finished/past/expired states are neutral").
_STATE_BADGE = {
    HomeworkState.OPEN: ('published', 'Open'),
    HomeworkState.CLOSED: ('reviewed', 'Closed'),
    HomeworkState.SCORED: ('reviewed', 'Scored'),
}


def _ordered_cohorts(course):
    """Course cohorts in selector order: start_date, then pk. Undated last."""
    cohorts = list(course.aisl_cohorts.all())
    cohorts.sort(key=lambda cohort: (
        cohort.start_date is None,
        cohort.start_date or datetime.date.min,
        cohort.pk,
    ))
    return cohorts


def _default_cohort(cohorts, today):
    """Dated cohort covering today, else the latest by start_date, else None.

    Overlapping windows resolve to the one that started latest (then pk).
    A dated cohort beats an undated one. None means the "all cohorts" view,
    used only when the course has no cohorts.
    """
    if not cohorts:
        return None
    current = [
        cohort for cohort in cohorts
        if cohort.start_date is not None
        and cohort.end_date is not None
        and cohort.start_date <= today <= cohort.end_date
    ]
    if current:
        return max(current, key=lambda cohort: (cohort.start_date, cohort.pk))
    return max(
        cohorts,
        key=lambda cohort: (
            cohort.start_date is not None,
            cohort.start_date or datetime.date.min,
            cohort.pk,
        ),
    )


def _selected_cohort(request, cohorts):
    """GET ``cohort`` pk, ``all``, or the default. An unknown id uses the default."""
    raw = (request.GET.get('cohort') or '').strip()
    if raw == 'all':
        return None
    if raw:
        try:
            cohort_id = int(raw)
        except (TypeError, ValueError):
            cohort_id = None
        if cohort_id is not None:
            match = next((cohort for cohort in cohorts if cohort.pk == cohort_id), None)
            if match is not None:
                return match
    return _default_cohort(cohorts, timezone.localdate())


def _module_position(module):
    """Syllabus position. Units on a top-level module sort before its children."""
    parent = module.parent
    if parent is None:
        return (module.sort_order, module.pk, 0, 0, 0)
    return (parent.sort_order, parent.pk, 1, module.sort_order, module.pk)


def _module_label(unit):
    if unit is None:
        return ''
    module = unit.module
    parent = module.parent
    if parent is not None:
        return f'{parent.title} / {module.title}'
    return module.title


def _homework_sort_key(homework, unit):
    """Curriculum order, then cohort, then title. Unmatched rows sort last.

    There is no FK from Homework to Unit (match is content_id to
    source_content_id), and a module is either a parent or a child, so
    this key is applied in Python rather than ORDER BY.
    """
    cohort = homework.cohort
    tail = (
        cohort.start_date is None,
        cohort.start_date or datetime.date.min,
        cohort.pk,
        homework.title,
        homework.pk,
    )
    if unit is None:
        return (1, 0, 0, 0, 0, 0, 0, 0, *tail)
    return (0, *_module_position(unit.module), unit.sort_order, unit.pk, *tail)


def _units_by_content_id(course, content_ids):
    """Earliest syllabus unit on this course for each homework content id."""
    if not content_ids:
        return {}
    units = (
        Unit.objects
        .filter(module__course=course, source_content_id__in=content_ids)
        .select_related('module', 'module__parent')
    )
    best = {}
    for unit in units:
        content_id = unit.source_content_id
        if content_id is None:
            continue
        current = best.get(content_id)
        position = (*_module_position(unit.module), unit.sort_order, unit.pk)
        if current is None or position < current[0]:
            best[content_id] = (position, unit)
    return {content_id: unit for content_id, (_position, unit) in best.items()}


@staff_required
def homework_list(request, course_id):
    """List a course's homeworks in syllabus order for one cohort (or all)."""
    course = get_object_or_404(Course, pk=course_id)
    cohorts = _ordered_cohorts(course)
    selected_cohort = _selected_cohort(request, cohorts)
    base = Homework.objects.filter(cohort__course=course)
    course_has_homeworks = base.exists()
    visible = base.filter(cohort=selected_cohort) if selected_cohort is not None else base
    homeworks = list(
        visible.select_related('cohort').annotate(submission_count=Count('submissions'))
    )
    units = _units_by_content_id(
        course,
        [homework.content_id for homework in homeworks if homework.content_id],
    )
    homework_rows = []
    for homework in homeworks:
        unit = units.get(homework.content_id) if homework.content_id else None
        badge_key, state_label = _STATE_BADGE.get(
            homework.state, ('reviewed', homework.get_state_display()),
        )
        homework_rows.append({
            'homework': homework,
            'module_label': _module_label(unit),
            'submission_count': homework.submission_count,
            'state_badge_key': badge_key,
            'state_label': state_label,
            '_sort': _homework_sort_key(homework, unit),
        })
    homework_rows.sort(key=lambda row: row['_sort'])
    for row in homework_rows:
        del row['_sort']
    return render(request, 'studio/courses/homeworks.html', {
        'course': course,
        'cohorts': cohorts,
        'selected_cohort': selected_cohort,
        'cohort_filter_empty': (
            selected_cohort is not None
            and not homework_rows
            and course_has_homeworks
        ),
        'homework_rows': homework_rows,
    })


@staff_required
def homework_submissions(request, homework_id):
    """Read-only list of every submission for one homework.

    Per-question answers and correctness are included -- this is the only
    place ``Answer.is_correct`` is ever rendered outside the model layer
    (see the "Why no answer_envelope" section of issue #1683). No scoring,
    re-score, or export controls.
    """
    homework = get_object_or_404(
        Homework.objects.select_related('cohort', 'cohort__course'),
        pk=homework_id,
    )
    submissions = (
        Submission.objects
        .filter(homework=homework)
        .select_related('student')
        .prefetch_related('answers__question')
        .order_by('-submitted_at')
    )
    submission_items = [
        {'submission': submission, 'answers': list(submission.answers.all())}
        for submission in submissions
    ]
    return render(request, 'studio/courses/homework_submissions.html', {
        'homework': homework,
        'course': homework.cohort.course,
        'submission_items': submission_items,
    })
