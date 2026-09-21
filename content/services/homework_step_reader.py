"""AISL adapter for the shared, cohort-scoped homework draft reader."""

from community_base.homework_steps.types import (
    Assignment,
    Eligibility,
    FinalField,
    Option,
)
from community_base.homework_steps.types import (
    Question as StepQuestion,
)
from django.core.exceptions import ValidationError
from django.utils.safestring import mark_safe

from content.models.homework import QuestionType, Submission
from content.services import course_units
from content.services.homework_step_sections import validate_question_bindings
from content.services.homework_submissions import save_submission
from content.utils.linkify import linkify_urls
from content.utils.markdown import render_markdown, sanitize_html

QUESTION_TYPES = {
    QuestionType.MULTIPLE_CHOICE: 'choice',
    QuestionType.CHECKBOXES: 'checkbox',
    QuestionType.FREE_FORM: 'short_text',
    QuestionType.FREE_FORM_LONG: 'long_text',
}


def question_key(question):
    """Use the authored stable identity, falling back for local questions."""
    return question.source_question_id or f'q-{question.pk}'


def _render_safe(markdown):
    return mark_safe(sanitize_html(linkify_urls(render_markdown(markdown))))


def build_assignment(homework, unit, user, *, context=None):
    """Construct the package descriptor without exposing answer keys/scores."""
    questions = list(homework.questions.all())
    keys = [question_key(question) for question in questions]
    introduction, rich_prompts, closing = validate_question_bindings(
        unit.homework or '', keys, unit.source_path or homework.source_path or unit.slug,
    )
    submission = None
    if user.is_authenticated:
        submission = (
            Submission.objects.filter(homework=homework, student=user)
            .prefetch_related('answers').first()
        )
    existing_answers = {}
    if submission:
        for answer in submission.answers.all():
            question = next((item for item in questions if item.pk == answer.question_id), None)
            if question is None:
                continue
            value = answer.answer_text or ''
            existing_answers[question_key(question)] = (
                [item.strip() for item in value.split(',') if item.strip()]
                if question.question_type == QuestionType.CHECKBOXES else value
            )
    step_questions = tuple(
        StepQuestion(
            key=question_key(question),
            prompt=_render_safe(rich_prompts[question_key(question)])
            if question_key(question) in rich_prompts else question.text,
            type=QUESTION_TYPES[question.question_type],
            options=tuple(
                Option(str(index), text)
                for index, text in enumerate(question.options_list, start=1)
            ),
        )
        for question in questions
    )
    return Assignment(
        key=f'aisl:homework:{homework.pk}',
        title=homework.title,
        questions=step_questions,
        introduction=_render_safe(introduction),
        instructions=_render_safe(closing),
        final_fields=(FinalField('homework_link', 'Homework link (optional)', 'url'),),
        existing_answers=existing_answers,
        existing_final_fields={
            'homework_link': submission.homework_link or '' if submission else '',
        },
        context=context or {},
    )


class AISLHomeworkAdapter:
    def __init__(self, homework, unit):
        self.homework = homework
        self.unit = unit

    def eligibility(self, request, assignment):
        if not request.user.is_authenticated:
            return Eligibility(False, False, False, 'Log in to work on this homework.')
        access = course_units.decide_course_unit_access(request.user, self.unit)
        if not access.has_access:
            return Eligibility(False, False, False, 'This course unit is locked.')
        drip = course_units.decide_course_unit_drip_lock(request.user, self.unit)
        if drip.is_locked:
            return Eligibility(False, False, False, 'This course unit is not available yet.')
        if course_units.resolve_homework_for_unit(self.unit, request.user) != self.homework:
            return Eligibility(False, False, False, 'This homework belongs to another cohort.')
        if not self.homework.is_accepting_submissions:
            reason = (
                'This homework is closed. Your saved answers are still available.'
                if self.homework.is_self_paced else
                'The deadline for this homework has passed. Your saved answers are still available.'
            )
            return Eligibility(True, False, False, reason)
        return Eligibility(True, True, True)

    def submit(self, request, assignment, answers, final_fields):
        if not self.eligibility(request, assignment).submit:
            raise ValidationError('This homework is closed; your draft was kept.')
        questions = {question_key(question): question for question in self.homework.questions.all()}
        converted = {}
        for key, value in answers.items():
            question = questions.get(key)
            if question is None:
                raise ValidationError('An answer no longer belongs to this homework.')
            if isinstance(value, list):
                answer = ','.join(sorted(value, key=lambda item: (len(item), item)))
            else:
                answer = value.strip()
            if answer:
                converted[question.pk] = answer
        return save_submission(
            self.homework, request.user,
            homework_link=final_fields.get('homework_link', '').strip(),
            answers_by_question_id=converted,
        )
