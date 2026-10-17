"""Validate homework frontmatter and choose cohort-specific sync targets."""

import datetime
from dataclasses import dataclass

from django.utils.dateparse import parse_datetime

from content.models.cohort import COHORT_MODE_COHORT, COHORT_MODE_SELF_PACED, Cohort
from content.models.homework import MAX_LEARNING_IN_PUBLIC_LINKS
from content.services.homework_step_sections import validate_question_bindings
from content.sync_parsers.common import GitHubSyncError, logger

_FORM_SETTING_KEYS = {
    'learning_in_public_cap',
    'homework_url_field',
    'time_spent_lectures_field',
    'time_spent_homework_field',
}


@dataclass(frozen=True)
class HomeworkSyncPlan:
    due_date: datetime.datetime | None
    questions: list
    stepper_enabled: bool
    form_settings: dict
    cohorts: list


def build_homework_sync_plan(unit, course, metadata, rel_path):
    """Return validated source values and ordered cohort targets."""
    raw_due_date = metadata.get('due_date')
    raw_questions = metadata.get('questions')
    if _is_prose_only(metadata, raw_due_date, raw_questions):
        return None
    due_date = _optional_due_date(raw_due_date, rel_path)
    questions = _questions_list(raw_questions, rel_path)
    stepper_enabled = _validate_stepper(unit, metadata, questions, rel_path)
    return HomeworkSyncPlan(
        due_date=due_date,
        questions=questions,
        stepper_enabled=stepper_enabled,
        form_settings=_parse_form_settings(metadata, rel_path),
        cohorts=resolve_homework_cohorts(course, rel_path),
    )


def _is_prose_only(metadata, raw_due_date, raw_questions):
    has_form_settings = any(key in metadata for key in _FORM_SETTING_KEYS)
    return (
        raw_due_date is None
        and not raw_questions
        and not metadata.get('homework_steps')
        and not has_form_settings
    )


def _optional_due_date(raw_value, rel_path):
    if raw_value is None:
        return None
    return _parse_due_date(raw_value, rel_path)


def _questions_list(raw_questions, rel_path):
    if isinstance(raw_questions, list):
        return raw_questions
    raise GitHubSyncError(
        f'Invalid homework in {rel_path}: questions: must be a list'
    )


def _validate_stepper(unit, metadata, questions, rel_path):
    enabled = metadata.get('homework_steps', False)
    if not isinstance(enabled, bool):
        raise GitHubSyncError(
            f'Invalid homework in {rel_path}: homework_steps must be true or false'
        )
    has_form_settings = any(key in metadata for key in _FORM_SETTING_KEYS)
    if has_form_settings and not enabled:
        raise GitHubSyncError(
            f'Invalid homework in {rel_path}: final-form settings require '
            'homework_steps: true'
        )
    if enabled:
        _validate_stepper_questions(unit, questions, rel_path)
    return enabled


def _validate_stepper_questions(unit, questions, rel_path):
    question_ids = []
    for entry in questions:
        question_id = None
        if isinstance(entry, dict):
            question_id = entry.get('id')
        question_ids.append(question_id)
    if not question_ids:
        raise GitHubSyncError(
            f'Invalid homework in {rel_path}: homework_steps needs questions'
        )
    try:
        validate_question_bindings(unit.homework, question_ids, rel_path)
    except ValueError as exc:
        raise GitHubSyncError(str(exc)) from exc
    for index, entry in enumerate(questions, start=1):
        _validate_stepper_choice(entry, index, rel_path)


def _validate_stepper_choice(entry, index, rel_path):
    raw_type = entry.get('type')
    if raw_type not in ('multiple_choice', 'checkboxes'):
        return
    options = entry.get('options')
    correct = str(entry.get('correct') or '').strip()
    selected = [value.strip() for value in correct.split(',')]
    if _valid_choice_answer(raw_type, options, correct, selected):
        return
    raise GitHubSyncError(
        f'Invalid homework steps in {rel_path}: Question {index} needs '
        'distinct options and approved answer keys within its options'
    )


def _valid_choice_answer(raw_type, options, correct, selected):
    if not isinstance(options, list) or not options or not correct:
        return False
    normalized = [str(option).strip() for option in options]
    if any(not option for option in normalized) or len(set(normalized)) != len(options):
        return False
    if raw_type == 'multiple_choice' and len(selected) != 1:
        return False
    if any(not value.isdigit() for value in selected) or len(set(selected)) != len(selected):
        return False
    return all(1 <= int(value) <= len(options) for value in selected)


def _parse_form_settings(metadata, rel_path):
    cap = metadata.get('learning_in_public_cap', 0)
    if type(cap) is not int or not 0 <= cap <= MAX_LEARNING_IN_PUBLIC_LINKS:
        raise GitHubSyncError(
            f'Invalid homework in {rel_path}: learning_in_public_cap must be '
            f'an integer from 0 to {MAX_LEARNING_IN_PUBLIC_LINKS}'
        )
    values = {'learning_in_public_cap': cap}
    defaults = (
        ('homework_url_field', True),
        ('time_spent_lectures_field', False),
        ('time_spent_homework_field', False),
    )
    for key, default in defaults:
        values[key] = _boolean_setting(metadata, key, default, rel_path)
    return values


def _boolean_setting(metadata, key, default, rel_path):
    value = metadata.get(key, default)
    if isinstance(value, bool):
        return value
    raise GitHubSyncError(
        f'Invalid homework in {rel_path}: {key} must be true or false'
    )


def _parse_due_date(raw_value, rel_path):
    parsed = None
    if isinstance(raw_value, datetime.datetime):
        parsed = raw_value
    elif isinstance(raw_value, str):
        parsed = parse_datetime(raw_value.strip())
    if parsed is None:
        raise GitHubSyncError(
            f'Invalid homework in {rel_path}: due_date {raw_value!r} is '
            'not a valid ISO-8601 datetime'
        )
    if parsed.tzinfo is not None:
        return parsed
    raise GitHubSyncError(
        f'Invalid homework in {rel_path}: due_date {raw_value!r} has no UTC '
        "offset (a naive datetime); use an explicit offset, e.g. '2026-09-27T21:59:00Z'"
    )


def resolve_homework_cohorts(course, rel_path):
    """Return the legacy target plus an active explicit self-paced target."""
    cohorts = list(Cohort.objects.filter(course=course))
    normal = _resolve_normal_homework_cohort(cohorts, course, rel_path)
    targets = []
    if normal is not None:
        targets.append(normal)
    for cohort in cohorts:
        if _is_active_explicit_self_paced(cohort) and cohort.pk not in {
            target.pk for target in targets
        }:
            targets.append(cohort)
    return targets


def _resolve_normal_homework_cohort(cohorts, course, rel_path):
    today = datetime.date.today()
    for cohort in cohorts:
        if _is_in_range_dated_cohort(cohort, today):
            return cohort
    if len(cohorts) == 1:
        only = cohorts[0]
        if only.mode != COHORT_MODE_SELF_PACED or only.is_active:
            return only
    logger.warning(
        'Skipping dated homework target for %s: no in-range cohort and %d '
        'cohort(s) defined for course %s (need exactly one to disambiguate).',
        rel_path, len(cohorts), course.slug,
    )
    return None


def _is_in_range_dated_cohort(cohort, today):
    if cohort.mode != COHORT_MODE_COHORT:
        return False
    if cohort.start_date is None or cohort.end_date is None:
        return False
    return cohort.start_date <= today <= cohort.end_date


def _is_active_explicit_self_paced(cohort):
    return (
        cohort.mode == COHORT_MODE_SELF_PACED
        and cohort.is_active
        and bool(cohort.external_key)
    )
