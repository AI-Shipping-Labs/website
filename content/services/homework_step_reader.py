"""AISL adapter for the shared, cohort-scoped homework draft reader."""

import hashlib
import math
from decimal import Decimal, InvalidOperation

from community_base.homework_steps.types import (
    AcceptedSubmission,
    Assignment,
    Eligibility,
    FinalField,
    Option,
)
from community_base.homework_steps.types import (
    Question as StepQuestion,
)
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.utils.safestring import mark_safe

from content.models.homework import HomeworkState, QuestionType, Submission
from content.services import completion as completion_service
from content.services import course_units
from content.services.homework_reveal import locked_after_submit, question_results
from content.services.homework_step_sections import (
    split_out_named_section,
    validate_question_bindings,
)
from content.services.homework_submissions import save_submission
from content.utils.linkify import linkify_urls
from content.utils.markdown import render_markdown, sanitize_html

QUESTION_TYPES = {
    QuestionType.MULTIPLE_CHOICE: 'choice',
    QuestionType.CHECKBOXES: 'checkbox',
    QuestionType.FREE_FORM: 'short_text',
    QuestionType.FREE_FORM_LONG: 'long_text',
}
LEARNING_IN_PUBLIC_KEY = 'learning-in-public'


def question_key(question):
    """Use the authored stable identity, falling back for local questions."""
    return question.source_question_id or f'q-{question.pk}'


def option_key(label):
    """Keep a choice's draft identity stable when source options move."""
    return 'option-' + hashlib.sha256(label.encode('utf-8')).hexdigest()[:24]


def _option_keys(question):
    keys = [option_key(label) for label in question.options_list]
    if len(keys) != len(set(keys)):
        raise ValueError(f'Duplicate option labels in homework question {question_key(question)}')
    return keys


def _submitted_value_to_key(question, value):
    """Convert the legacy numeric storage shape to stable draft keys."""
    if question.question_type not in (QuestionType.MULTIPLE_CHOICE, QuestionType.CHECKBOXES):
        return value
    keys = _option_keys(question)

    def resolve(index):
        try:
            position = int(index)
        except (ValueError, IndexError):
            return ''
        return keys[position - 1] if 1 <= position <= len(keys) else ''

    if question.question_type == QuestionType.CHECKBOXES:
        return [key for item in value.split(',') if (key := resolve(item.strip()))]
    return resolve(value) if value else ''


def _render_safe(markdown):
    return mark_safe(sanitize_html(linkify_urls(render_markdown(markdown))))


def assignment_key(homework):
    """The shared draft/assignment identity for one cohort ``Homework`` row."""
    return f'aisl:homework:{homework.pk}'


def homework_availability(homework, submission):
    """Map AISL homework state onto the shared ``open|closed|scored`` value.

    The one mapping used by the stepper (``build_assignment``) and the
    navigation row status (``content.services.homework_rows.
    annotate_homework_rows``), so a row label and the stepper page's state
    label can never disagree. Acceptance follows ``state`` only (issue
    #1917): a past-due ``OPEN`` homework stays ``open``; only an operator
    setting ``CLOSED`` lands on ``closed``, and ``SCORED`` (or a self-paced
    learner locked after submitting) on ``scored``.
    """
    if homework.state == HomeworkState.SCORED or locked_after_submit(homework, submission):
        return 'scored'
    if not homework.is_accepting_submissions:
        return 'closed'
    return 'open'


def _final_fields(homework, submission):
    """The configured final fields and the submission's accepted values."""
    final_fields = []
    existing_final_fields = {}
    if homework.homework_url_field:
        final_fields.append(FinalField(
            'homework_link', 'Homework URL', 'url', required=True,
        ))
        existing_final_fields['homework_link'] = (
            submission.homework_link or '' if submission else ''
        )
    if homework.time_spent_lectures_field:
        final_fields.append(FinalField(
            'time_spent_lectures',
            'Time spent on lectures (hours) (optional)',
        ))
        existing_final_fields['time_spent_lectures'] = (
            '' if not submission or submission.time_spent_lectures is None
            else str(submission.time_spent_lectures)
        )
    if homework.time_spent_homework_field:
        final_fields.append(FinalField(
            'time_spent_homework',
            'Time spent on homework (hours) (optional)',
        ))
        existing_final_fields['time_spent_homework'] = (
            '' if not submission or submission.time_spent_homework is None
            else str(submission.time_spent_homework)
        )
    return tuple(final_fields), existing_final_fields


def _existing_answers(homework, questions, submission):
    """The accepted submission's answers keyed by the stepper's step keys."""
    existing_answers = {}
    if submission:
        for answer in submission.answers.all():
            question = next((item for item in questions if item.pk == answer.question_id), None)
            if question is None:
                continue
            value = answer.answer_text or ''
            existing_answers[question_key(question)] = _submitted_value_to_key(question, value)
    if homework.learning_in_public_cap:
        existing_public_links = (
            submission.learning_in_public_links
            if submission and isinstance(submission.learning_in_public_links, list)
            else []
        )
        existing_answers[LEARNING_IN_PUBLIC_KEY] = '\n'.join(existing_public_links)
    return existing_answers


def _accepted_submission(existing_answers, existing_final_fields, submission):
    if submission is None:
        return None
    return AcceptedSubmission(
        answers=existing_answers,
        final_fields=existing_final_fields,
        submitted_at=submission.submitted_at,
    )


def build_state_assignment(homework, questions, submission):
    """A prompt-free descriptor carrying only what the shared state needs.

    ``calculate_homework_state`` compares a draft against the accepted
    snapshot using question keys and types, final fields, and availability.
    This builds exactly those from the same helpers as ``build_assignment``
    without rendering any markdown, so navigation rows can compute status
    for many homework rows from batch-loaded data.
    """
    step_questions = tuple(
        StepQuestion(
            key=question_key(question),
            prompt='',
            type=QUESTION_TYPES[question.question_type],
        )
        for question in questions
    )
    if homework.learning_in_public_cap:
        step_questions += (
            StepQuestion(key=LEARNING_IN_PUBLIC_KEY, prompt='', type='long_text'),
        )
    existing_answers = _existing_answers(homework, questions, submission)
    final_fields, existing_final_fields = _final_fields(homework, submission)
    return Assignment(
        key=assignment_key(homework),
        title=homework.title,
        questions=step_questions,
        final_fields=final_fields,
        existing_answers=existing_answers,
        existing_final_fields=existing_final_fields,
        has_submission=bool(submission),
        availability=homework_availability(homework, submission),
        accepted_submission=_accepted_submission(
            existing_answers, existing_final_fields, submission,
        ),
    )


def build_assignment(homework, unit, user, *, context=None):
    """Construct the package descriptor.

    Answer keys and correctness reach the descriptor only through
    ``question_results``, and only once ``homework_reveal`` reveals them:
    after submit for self-paced homework, after scoring for a dated cohort.
    """
    questions = list(homework.questions.all())
    keys = [question_key(question) for question in questions]
    introduction, rich_prompts, closing = validate_question_bindings(
        unit.homework or '', keys, unit.source_path or homework.source_path or unit.slug,
    )
    learning_in_public_cap = homework.learning_in_public_cap
    learning_guidance = ''
    if learning_in_public_cap:
        closing, learning_guidance = split_out_named_section(
            closing, 'Learning in Public',
        )
    submission = None
    if user.is_authenticated:
        submission = (
            Submission.objects.filter(homework=homework, student=user)
            .prefetch_related('answers').first()
        )
    existing_answers = _existing_answers(homework, questions, submission)
    step_questions = tuple(
        StepQuestion(
            key=question_key(question),
            prompt=_render_safe(rich_prompts[question_key(question)])
            if question_key(question) in rich_prompts else question.text,
            type=QUESTION_TYPES[question.question_type],
            options=tuple(
                Option(key, text)
                for key, text in zip(_option_keys(question), question.options_list, strict=True)
            ),
        )
        for question in questions
    )
    if learning_in_public_cap:
        guidance = learning_guidance or (
            'Share your progress in public if you would like.'
        )
        learning_prompt = _render_safe(f'## Learning in Public\n\n{guidance}')
        step_questions += (
            StepQuestion(
                key=LEARNING_IN_PUBLIC_KEY,
                prompt=learning_prompt,
                type='long_text',
                step_label='Learning in Public',
            ),
        )
    final_fields, existing_final_fields = _final_fields(homework, submission)
    availability = homework_availability(homework, submission)
    results_by_id = question_results(homework, submission)
    revealed_results = (
        None if results_by_id is None else {
            question_key(question): results_by_id[question.pk]
            for question in questions
        }
    )
    return Assignment(
        key=assignment_key(homework),
        title=homework.title,
        questions=step_questions,
        introduction=_render_safe(introduction),
        instructions=_render_safe(closing),
        final_fields=final_fields,
        existing_answers=existing_answers,
        existing_final_fields=existing_final_fields,
        context={
            **(context or {}),
            'homework_is_submitted': bool(submission),
            'learning_in_public_cap': learning_in_public_cap,
        },
        has_submission=bool(submission),
        availability=availability,
        accepted_submission=_accepted_submission(
            existing_answers, existing_final_fields, submission,
        ),
        question_results=revealed_results,
    )


class AISLHomeworkAdapter:
    def __init__(self, homework, unit, *, cohort=None):
        self.homework = homework
        self.unit = unit
        self.cohort = cohort

    def eligibility(self, request, assignment):
        if not request.user.is_authenticated:
            return Eligibility(False, False, False, 'Log in to work on this homework.')
        access = course_units.decide_course_unit_access(request.user, self.unit)
        if not access.has_access:
            return Eligibility(False, False, False, 'This course unit is locked.')
        if self.cohort is None:
            drip = course_units.decide_course_unit_drip_lock(request.user, self.unit)
        else:
            drip = course_units.decide_course_unit_drip_lock(
                request.user, self.unit, cohort=self.cohort,
            )
        if drip.is_locked:
            return Eligibility(False, False, False, 'This course unit is not available yet.')
        if course_units.resolve_homework_for_unit(
            self.unit, request.user, cohort=self.cohort,
        ) != self.homework:
            return Eligibility(False, False, False, 'This homework belongs to another cohort.')
        submission = Submission.objects.filter(
            homework=self.homework, student=request.user,
        ).first()
        if locked_after_submit(self.homework, submission):
            return Eligibility(
                True, False, False,
                'You have submitted this homework. Your results are shown with each answer.',
            )
        if not self.homework.is_accepting_submissions:
            # is_accepting_submissions False now means CLOSED/SCORED only
            # (issue #1917) -- never claim a deadline passed.
            return Eligibility(
                True, False, False,
                'This homework is closed. Your saved answers are still available.',
            )
        return Eligibility(True, True, True)

    def submit(self, request, assignment, answers, final_fields):
        if not self.eligibility(request, assignment).submit:
            raise ValidationError('This homework is closed; your draft was kept.')
        existing_submission = Submission.objects.filter(
            homework=self.homework, student=request.user,
        ).first()
        existing_links = (
            existing_submission.learning_in_public_links
            if existing_submission
            and isinstance(existing_submission.learning_in_public_links, list)
            else []
        )
        learning_in_public_links = (
            _parse_public_links(
                answers.get(LEARNING_IN_PUBLIC_KEY, ''),
                self.homework.learning_in_public_cap,
            )
            if self.homework.learning_in_public_cap else existing_links
        )
        questions = {question_key(question): question for question in self.homework.questions.all()}
        converted = {}
        for key, value in answers.items():
            if key == LEARNING_IN_PUBLIC_KEY:
                continue
            question = questions.get(key)
            if question is None:
                raise ValidationError('An answer no longer belongs to this homework.')
            if question.question_type in (QuestionType.MULTIPLE_CHOICE, QuestionType.CHECKBOXES):
                positions = {item: str(index) for index, item in enumerate(
                    _option_keys(question), start=1,
                )}
                selected = value if isinstance(value, list) else [value]
                if any(item not in positions for item in selected):
                    raise ValidationError('An answer option changed. Review and save it again.')
                answer = ','.join(sorted((positions[item] for item in selected), key=int))
            else:
                answer = value.strip()
            if answer:
                converted[question.pk] = answer
        submission = save_submission(
            self.homework, request.user,
            homework_link=(
                final_fields.get('homework_link', '').strip()
                if self.homework.homework_url_field
                else existing_submission.homework_link if existing_submission else ''
            ),
            learning_in_public_links=learning_in_public_links,
            time_spent_lectures=(
                _parse_optional_hours(
                    final_fields.get('time_spent_lectures', ''),
                    'Time spent on lectures',
                )
                if self.homework.time_spent_lectures_field
                else existing_submission.time_spent_lectures if existing_submission else None
            ),
            time_spent_homework=(
                _parse_optional_hours(
                    final_fields.get('time_spent_homework', ''),
                    'Time spent on homework',
                )
                if self.homework.time_spent_homework_field
                else existing_submission.time_spent_homework if existing_submission else None
            ),
            answers_by_question_id=converted,
        )
        completion_service.mark_completed(request.user, self.unit)
        return submission


def _parse_public_links(answer, limit):
    """Validate the optional newline-separated public links answer."""
    if not isinstance(answer, str):
        raise ValidationError('Enter public links one per line.')
    links = [line.strip() for line in answer.splitlines() if line.strip()]
    if len(links) > limit:
        noun = 'link' if limit == 1 else 'links'
        raise ValidationError(f'Add no more than {limit} public {noun}.')
    validate_url = URLValidator(schemes=['http', 'https'])
    for link in links:
        try:
            validate_url(link)
        except ValidationError as exc:
            raise ValidationError('Enter a valid http or https link for each public link.') from exc
    return links


def _parse_optional_hours(value, label):
    """Parse an optional non-negative hour value without accepting infinities."""
    raw = str(value or '').strip()
    if not raw:
        return None
    try:
        parsed = Decimal(raw)
    except InvalidOperation as exc:
        raise ValidationError(f'{label} must be a non-negative number of hours.') from exc
    if not parsed.is_finite() or parsed < 0:
        raise ValidationError(f'{label} must be a non-negative number of hours.')
    result = float(parsed)
    if not math.isfinite(result):
        raise ValidationError(f'{label} must be a non-negative number of hours.')
    return result
