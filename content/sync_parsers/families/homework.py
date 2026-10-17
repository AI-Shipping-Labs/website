"""Sync ``questions:``/``due_date:`` frontmatter into Homework/Question rows.

Issue #1683, tranche 1. Extends the same homework unit markdown file
``_sync_module_units`` already parses (``is_homework: true`` /
``kind: homework``, see ``content/sync_parsers/families/courses.py``) --
one file per homework, no second file to keep in sync.
"""

from content.models.cohort import COHORT_MODE_SELF_PACED
from content.sync_parsers.common import GitHubSyncError
from content.sync_parsers.families.homework_plan import build_homework_sync_plan

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
    """Upsert every cohort-specific Homework/Question row backing ``unit``."""
    plan = build_homework_sync_plan(unit, course, metadata, rel_path)
    if plan is None:
        return
    if not plan.cohorts:
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
    for cohort in plan.cohorts:
        _sync_homework_target(unit, cohort, plan, rel_path)


def _sync_homework_target(unit, cohort, plan, rel_path):
    from content.models.homework import Homework

    due_date = plan.due_date
    if cohort.mode == COHORT_MODE_SELF_PACED:
        due_date = None
    defaults = {
        'title': unit.title,
        'due_date': due_date,
        'content_id': unit.content_id,
        'source_repo': unit.source_repo,
        'source_path': rel_path,
        'stepper_enabled': plan.stepper_enabled,
        **plan.form_settings,
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
    _sync_questions(homework, plan.questions, rel_path)


def _sync_questions(homework, questions, rel_path):
    seen_question_ids = set()
    for index, entry in enumerate(questions):
        source_question_id = _sync_one_question(
            homework, entry, index, rel_path,
        )
        seen_question_ids.add(source_question_id)
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
