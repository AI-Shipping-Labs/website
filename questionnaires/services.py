"""Service helpers for the questionnaire system (issue #800).

``build_response_questions`` is the shared seam #802 (per-member
customization) and #803 (bulk feedback responses) both call to
materialize a response's question set from the questionnaire's base
question set. It is pure-Python and ORM-only -- no HTTP, no AI.
"""

from questionnaires.models import (
    Answer,
    AnswerOptionText,
    ResponseQuestion,
    ResponseQuestionOption,
)

# Question types whose answer lives in ``text_value``.
_TEXT_TYPES = frozenset({'text', 'long_text'})
# Question types whose answer lives in ``number_value``.
_NUMBER_TYPES = frozenset({'scale', 'number'})
# Question types whose answer lives in ``selected_options``.
_CHOICE_TYPES = frozenset({'single_choice', 'multiple_choice'})


def build_response_questions(response):
    """Copy base questions + options onto ``response`` as snapshots.

    For a freshly created :class:`~questionnaires.models.Response`, copy
    every base :class:`~questionnaires.models.Question` (and its
    :class:`~questionnaires.models.QuestionOption` rows) into
    :class:`~questionnaires.models.ResponseQuestion` /
    :class:`~questionnaires.models.ResponseQuestionOption` rows.

    Idempotent: if the response already has any response-questions this is
    a no-op and returns the empty list, so re-running never duplicates
    rows. Returns the list of created ``ResponseQuestion`` instances.

    Snapshotting (not live FK reads) means a later edit to a base question
    never silently rewrites a response a member has already started.
    """
    if response.response_questions.exists():
        return []

    base_questions = response.questionnaire.questions.all()
    created = []
    for base in base_questions:
        rq = ResponseQuestion.objects.create(
            response=response,
            source_question=base,
            question_type=base.question_type,
            prompt=base.prompt,
            help_text=base.help_text,
            is_required=base.is_required,
            order=base.order,
            scale_min=base.scale_min,
            scale_max=base.scale_max,
        )
        for opt in base.options.all():
            ResponseQuestionOption.objects.create(
                response_question=rq,
                source_option=opt,
                label=opt.label,
                allows_free_text=opt.allows_free_text,
                order=opt.order,
            )
        created.append(rq)
    return created


def _field_name(response_question):
    """The POST field name for a response-question's answer input."""
    return f'question_{response_question.pk}'


def _option_text_field_name(response_question, option):
    """The POST field name for free text attached to a choice option."""
    return f'question_{response_question.pk}_option_{option.pk}_text'


class AnswerSaveError(Exception):
    """Raised when posted answers fail validation (out-of-range, etc.).

    ``field_errors`` maps ``ResponseQuestion`` pk -> message so the
    caller can re-render the form with HTTP 400 and per-field errors.
    """

    def __init__(self, field_errors):
        self.field_errors = field_errors
        super().__init__('Answer validation failed')


def build_response_form_rows(response, *, post_data=None, field_errors=None):
    """Build the per-question render rows for the member fill-in form.

    Shared seam for #803 (sprint feedback) and #802 (onboarding): one
    row per ``ResponseQuestion`` carrying everything the reusable
    ``questionnaires/_response_form.html`` fragment needs to render the
    correct input by type and pre-fill the current value. Pre-fill comes
    from ``post_data`` (re-render after a validation error) when present,
    otherwise from any existing ``Answer`` rows (resume a partial draft).

    Each row dict has: ``question`` (the ResponseQuestion), ``field_name``,
    ``options`` (each ``{option, selected}`` for choice types),
    ``text_value`` / ``number_value`` for the non-choice types, and
    ``error`` (a per-field message or empty).
    """
    field_errors = field_errors or {}
    answers_by_question = {
        answer.question_id: answer
        for answer in response.answers.prefetch_related('selected_options').all()
    }
    option_texts_by_answer = {}
    if post_data is None:
        for answer in response.answers.prefetch_related('option_texts').all():
            option_texts_by_answer[answer.pk] = {
                item.selected_option_id: item.text_value
                for item in answer.option_texts.all()
            }
    rows = []
    for rq in response.response_questions.prefetch_related('options').all():
        field_name = _field_name(rq)
        existing = answers_by_question.get(rq.pk)
        text_value = ''
        number_value = ''
        selected_ids = set()
        if post_data is not None:
            if rq.question_type in _TEXT_TYPES:
                text_value = post_data.get(field_name, '')
            elif rq.question_type in _NUMBER_TYPES:
                number_value = post_data.get(field_name, '')
            elif rq.question_type in _CHOICE_TYPES:
                selected_ids = {
                    int(v) for v in post_data.getlist(field_name) if v.isdigit()
                }
        elif existing is not None:
            text_value = existing.text_value or ''
            number_value = (
                '' if existing.number_value is None else str(existing.number_value)
            )
            selected_ids = {opt.pk for opt in existing.selected_options.all()}

        options = []
        existing_option_texts = (
            option_texts_by_answer.get(existing.pk, {}) if existing else {}
        )
        for opt in rq.options.all():
            free_text_name = _option_text_field_name(rq, opt)
            if post_data is not None:
                free_text_value = post_data.get(free_text_name, '')
            else:
                free_text_value = existing_option_texts.get(opt.pk, '')
            options.append({
                'option': opt,
                'selected': opt.pk in selected_ids,
                'free_text_name': free_text_name,
                'free_text_value': free_text_value,
            })
        rows.append({
            'question': rq,
            'field_name': field_name,
            'text_value': text_value,
            'number_value': number_value,
            'options': options,
            'error': field_errors.get(rq.pk, ''),
        })
    return rows


def save_response_answers(response, post_data, *, require_choice_free_text=False):
    """Upsert one ``Answer`` per ``ResponseQuestion`` from posted data.

    Shared by the save and the submit paths (#803, reusable by #802).
    Storage by type: ``text``/``long_text`` -> ``text_value``;
    ``scale``/``number`` -> ``number_value`` (validated as an integer
    inside ``[scale_min, scale_max]`` when those bounds are set);
    ``single_choice`` -> one selected option; ``multiple_choice`` ->
    zero-or-more selected options. Choice options with ``allows_free_text``
    store their attached text on ``AnswerOptionText``.

    Validation errors (non-integer / out-of-range numbers, unknown
    option ids) raise :class:`AnswerSaveError` BEFORE any write, so a bad
    POST never persists a partial mix. Returns nothing on success.
    """
    response_questions = list(
        response.response_questions.prefetch_related('options').all(),
    )
    field_errors = {}
    # Stage parsed values first so a single bad field aborts the whole save.
    staged = []
    for rq in response_questions:
        field_name = _field_name(rq)
        qtype = rq.question_type
        if qtype in _TEXT_TYPES:
            staged.append((rq, 'text', post_data.get(field_name, '').strip()))
        elif qtype in _NUMBER_TYPES:
            raw = post_data.get(field_name, '').strip()
            if raw == '':
                staged.append((rq, 'number', None))
                continue
            try:
                value = int(raw)
            except (TypeError, ValueError):
                field_errors[rq.pk] = 'Enter a whole number.'
                continue
            if rq.scale_min is not None and value < rq.scale_min:
                field_errors[rq.pk] = (
                    f'Enter a number of at least {rq.scale_min}.'
                )
                continue
            if rq.scale_max is not None and value > rq.scale_max:
                field_errors[rq.pk] = (
                    f'Enter a number no greater than {rq.scale_max}.'
                )
                continue
            staged.append((rq, 'number', value))
        elif qtype in _CHOICE_TYPES:
            valid_ids = {opt.pk for opt in rq.options.all()}
            raw_ids = {
                int(v) for v in post_data.getlist(field_name) if v.isdigit()
            }
            if qtype == 'single_choice' and len(raw_ids) > 1:
                field_errors[rq.pk] = 'Pick only one option.'
                continue
            unknown = raw_ids - valid_ids
            if unknown:
                field_errors[rq.pk] = 'Pick a valid option.'
                continue
            option_texts = {}
            for opt in rq.options.all():
                if not opt.allows_free_text or opt.pk not in raw_ids:
                    continue
                text_value = post_data.get(
                    _option_text_field_name(rq, opt), '',
                ).strip()
                if require_choice_free_text and not text_value:
                    field_errors[rq.pk] = (
                        f'Describe your "{opt.label}" answer.'
                    )
                    break
                option_texts[opt.pk] = text_value
            else:
                staged.append((rq, 'choice', raw_ids, option_texts))

    if field_errors:
        raise AnswerSaveError(field_errors)

    for item in staged:
        rq, kind, value = item[:3]
        answer, _ = Answer.objects.get_or_create(response=response, question=rq)
        if kind == 'text':
            answer.text_value = value
            answer.number_value = None
            answer.save(update_fields=['text_value', 'number_value', 'updated_at'])
            answer.selected_options.clear()
            AnswerOptionText.objects.filter(answer=answer).delete()
        elif kind == 'number':
            answer.text_value = ''
            answer.number_value = value
            answer.save(update_fields=['text_value', 'number_value', 'updated_at'])
            answer.selected_options.clear()
            AnswerOptionText.objects.filter(answer=answer).delete()
        else:  # choice
            option_texts = item[3]
            answer.text_value = ''
            answer.number_value = None
            answer.save(update_fields=['text_value', 'number_value', 'updated_at'])
            if value:
                answer.selected_options.set(
                    ResponseQuestionOption.objects.filter(pk__in=value),
                )
            else:
                answer.selected_options.clear()
            AnswerOptionText.objects.filter(answer=answer).exclude(
                selected_option_id__in=value,
            ).delete()
            for option_id, text_value in option_texts.items():
                if text_value:
                    AnswerOptionText.objects.update_or_create(
                        answer=answer,
                        selected_option_id=option_id,
                        defaults={'text_value': text_value},
                    )
                else:
                    AnswerOptionText.objects.filter(
                        answer=answer,
                        selected_option_id=option_id,
                    ).delete()


def find_unanswered_required(response):
    """Return required ``ResponseQuestion`` rows with no non-empty answer.

    Used by the submit path: a required question is satisfied when its
    ``Answer`` has a non-empty ``text_value``, a non-null
    ``number_value``, or at least one selected option. Returns the list
    of unsatisfied required questions (empty list when all are answered).
    """
    answers_by_question = {
        answer.question_id: answer
        for answer in response.answers.prefetch_related('selected_options').all()
    }
    missing = []
    for rq in response.response_questions.all():
        if not rq.is_required:
            continue
        answer = answers_by_question.get(rq.pk)
        if answer is None:
            missing.append(rq)
            continue
        if rq.question_type in _TEXT_TYPES:
            if not (answer.text_value or '').strip():
                missing.append(rq)
        elif rq.question_type in _NUMBER_TYPES:
            if answer.number_value is None:
                missing.append(rq)
        elif rq.question_type in _CHOICE_TYPES:
            if not answer.selected_options.exists():
                missing.append(rq)
    return missing
