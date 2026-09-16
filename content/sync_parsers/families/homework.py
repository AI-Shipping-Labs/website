"""Sync ``questions:``/``due_date:`` frontmatter into Homework/Question rows.

Issue #1683, tranche 1. Extends the same homework unit markdown file
``_sync_module_units`` already parses (``is_homework: true`` /
``kind: homework``, see ``content/sync_parsers/families/courses.py``) --
one file per homework, no second file to keep in sync.
"""

import datetime

from django.utils.dateparse import parse_datetime

from content.sync_parsers.common import GitHubSyncError, logger

_QUESTION_TYPE_MAP = {
    'multiple_choice': 'MC',
    'free_form': 'FF',
    'free_form_long': 'FL',
    'checkboxes': 'CB',
}
_ANSWER_TYPE_MAP = {
    'any': 'ANY',
    'float': 'FLT',
    'integer': 'INT',
    'exact_string': 'EXS',
    'contains_string': 'CTS',
}


def sync_unit_homework(unit, course, metadata, rel_path, stats):
    """Upsert the ``Homework``/``Question`` rows backing ``unit``, if any.

    No-op when the file carries neither ``due_date:`` nor ``questions:`` --
    the common case for homework units that predate this issue, which must
    keep rendering prose-only with no submission form (the explicit
    backward-compatibility contract).

    Raises :class:`GitHubSyncError` for a malformed ``due_date:`` or
    ``questions:`` entry. The caller's per-file exception handler records
    the error against this file only and continues syncing the rest of the
    course -- one bad homework file never aborts the whole sync run.
    """
    from content.models.homework import Homework

    raw_due_date = metadata.get('due_date')
    raw_questions = metadata.get('questions')

    if raw_due_date is None and not raw_questions:
        return

    if raw_due_date is None:
        raise GitHubSyncError(
            f'Invalid homework in {rel_path}: due_date is required when '
            f'questions: are present'
        )

    due_date = _parse_due_date(raw_due_date, rel_path)

    if not isinstance(raw_questions, list):
        raise GitHubSyncError(
            f'Invalid homework in {rel_path}: questions: must be a list'
        )

    cohort = _resolve_homework_cohort(course, rel_path)
    if cohort is None:
        stats['errors'].append({
            'file': rel_path,
            'severity': 'info',
            'error': (
                'Skipping homework sync: no cohort resolves for this '
                'course (no in-range cohort, and not exactly one cohort '
                'defined to fall back to).'
            ),
        })
        return

    defaults = {
        'title': unit.title,
        'due_date': due_date,
        'content_id': unit.content_id,
        'source_repo': unit.source_repo,
        'source_path': rel_path,
    }

    homework = Homework.objects.filter(
        content_id=unit.content_id, cohort=cohort,
    ).first()
    if homework is None:
        homework = Homework.objects.filter(cohort=cohort, slug=unit.slug).first()

    if homework is None:
        homework = Homework(cohort=cohort, slug=unit.slug, **defaults)
        homework.save()
    else:
        homework.slug = unit.slug
        for key, value in defaults.items():
            setattr(homework, key, value)
        homework.save()

    seen_question_ids = set()
    for index, entry in enumerate(raw_questions):
        source_question_id = _sync_one_question(
            homework, entry, index, rel_path,
        )
        seen_question_ids.add(source_question_id)

    # A questions: entry removed from frontmatter deletes the corresponding
    # Question (and, via CASCADE, its Answer rows) on next sync.
    homework.questions.exclude(
        source_question_id__in=seen_question_ids,
    ).delete()


def _sync_one_question(homework, entry, index, rel_path):
    """Upsert one ``questions:`` entry; return its ``source_question_id``."""
    from content.models.homework import Question

    if not isinstance(entry, dict):
        raise GitHubSyncError(
            f'Invalid homework in {rel_path}: questions[{index}] must be a '
            f'mapping'
        )

    source_question_id = str(entry.get('id') or '').strip()
    if not source_question_id:
        raise GitHubSyncError(
            f'Invalid homework in {rel_path}: questions[{index}] is '
            f'missing id'
        )

    question_text = str(entry.get('text') or '').strip()
    if not question_text:
        raise GitHubSyncError(
            f'Invalid homework in {rel_path}: question '
            f'{source_question_id!r} is missing text'
        )

    raw_type = str(entry.get('type') or '').strip()
    question_type = _QUESTION_TYPE_MAP.get(raw_type)
    if question_type is None:
        raise GitHubSyncError(
            f'Invalid homework in {rel_path}: question '
            f'{source_question_id!r} has unknown type {raw_type!r}'
        )

    answer_type = ''
    if question_type in ('FF', 'FL'):
        raw_answer_type = str(entry.get('answer_type') or 'any').strip()
        answer_type = _ANSWER_TYPE_MAP.get(raw_answer_type)
        if answer_type is None:
            raise GitHubSyncError(
                f'Invalid homework in {rel_path}: question '
                f'{source_question_id!r} has unknown answer_type '
                f'{raw_answer_type!r}'
            )

    options = entry.get('options') or []
    if not isinstance(options, list):
        raise GitHubSyncError(
            f'Invalid homework in {rel_path}: question '
            f'{source_question_id!r} options: must be a list'
        )

    try:
        score = int(entry.get('score', 1))
    except (TypeError, ValueError):
        raise GitHubSyncError(
            f'Invalid homework in {rel_path}: question '
            f'{source_question_id!r} has a non-integer score'
        ) from None

    question_defaults = {
        'text': question_text,
        'question_type': question_type,
        'answer_type': answer_type,
        'possible_answers': '\n'.join(str(o) for o in options),
        'correct_answer': str(entry.get('correct', '') or ''),
        'scores_for_correct_answer': score,
        'source_repo': homework.source_repo,
        'source_path': rel_path,
    }

    question = Question.objects.filter(
        homework=homework, source_question_id=source_question_id,
    ).first()
    if question is None:
        Question.objects.create(
            homework=homework,
            source_question_id=source_question_id,
            **question_defaults,
        )
    else:
        for key, value in question_defaults.items():
            setattr(question, key, value)
        question.save()

    return source_question_id


def _parse_due_date(raw_value, rel_path):
    """Parse ``due_date:`` into an aware ``datetime``.

    Rejects a naive datetime (no UTC offset) with a :class:`GitHubSyncError`
    naming the file -- ISO-8601 with an explicit offset is required, e.g.
    ``'2026-09-27T21:59:00Z'``.
    """
    if isinstance(raw_value, datetime.datetime):
        parsed = raw_value
    elif isinstance(raw_value, str):
        parsed = parse_datetime(raw_value.strip())
    else:
        parsed = None

    if parsed is None:
        raise GitHubSyncError(
            f'Invalid homework in {rel_path}: due_date {raw_value!r} is '
            f'not a valid ISO-8601 datetime'
        )
    if parsed.tzinfo is None:
        raise GitHubSyncError(
            f'Invalid homework in {rel_path}: due_date {raw_value!r} has '
            f"no UTC offset (a naive datetime); use an explicit offset, "
            f"e.g. '2026-09-27T21:59:00Z'"
        )
    return parsed


def _resolve_homework_cohort(course, rel_path):
    """Resolve the target ``Cohort`` for a homework file's questions.

    Order: the course's dated (``mode='cohort'``) cohort whose date range
    contains today; else, when the course has exactly one cohort overall
    (dated or ``mode='self_paced'``), that cohort; otherwise ``None`` --
    the caller logs a warning and skips homework sync for this file. Not a
    hard sync failure: unrelated units in the same course still sync.

    Issue #1674 made ``Cohort.start_date``/``end_date`` nullable
    (``mode='self_paced'`` cohorts carry no dates), so the in-range check
    only considers dated cohorts -- a self-paced cohort can never be "in
    range" by definition, but still qualifies for the single-cohort
    fallback.
    """
    from content.models.cohort import COHORT_MODE_COHORT, Cohort

    today = datetime.date.today()
    cohorts = list(Cohort.objects.filter(course=course))

    in_range = [
        c for c in cohorts
        if c.mode == COHORT_MODE_COHORT
        and c.start_date is not None
        and c.end_date is not None
        and c.start_date <= today <= c.end_date
    ]
    if in_range:
        return in_range[0]

    if len(cohorts) == 1:
        return cohorts[0]

    logger.warning(
        'Skipping homework sync for %s: no in-range cohort and %d cohort(s) '
        'defined for course %s (need exactly one to disambiguate).',
        rel_path, len(cohorts), course.slug,
    )
    return None
