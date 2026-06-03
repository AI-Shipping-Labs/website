"""Serializers for the read-only onboarding API (issue #837).

Pure functions (no DRF) that convert ``questionnaires`` ORM rows into the
JSON shapes documented in the OpenAPI spec. Mirrors
``api/serializers/users.py`` (plain dict builders, an ``_isoformat_or_none``
helper).

Two surfaces:

A. Survey definition -- ``serialize_questionnaire`` / ``serialize_persona``
   read the BASE template (``Question`` / ``QuestionOption``) so the API
   describes the live survey shape, not any member's response.

B. Member responses -- ``serialize_response`` reads the SNAPSHOT layer
   (``ResponseQuestion`` / ``Answer`` / ``ResponseQuestionOption``), never
   the base ``Question``, so the prompt/options/answers returned are
   exactly what the member saw at fill-in time. Editing or deleting a base
   ``Question`` after submit never changes the API output. We deliberately
   do NOT reuse ``Answer.display_value`` (it joins choice labels into one
   comma string, flattening the list the plan-generation feed needs).
"""

# Question types whose answer lives in ``Answer.text_value``.
_TEXT_TYPES = frozenset({"text", "long_text"})
# Question types whose answer lives in ``Answer.number_value``.
_NUMBER_TYPES = frozenset({"scale", "number"})
# Single-choice yields one label; multiple-choice yields a list of labels.
_SINGLE_CHOICE = "single_choice"
_MULTIPLE_CHOICE = "multiple_choice"


def _isoformat_or_none(value):
    if value is None:
        return None
    return value.isoformat()


# ---- A. Survey definition --------------------------------------------------


def serialize_question_option(option):
    """One base ``QuestionOption`` row -> ``{label, order}``."""
    return {"label": option.label, "order": option.order}


def serialize_question(question):
    """One base ``Question`` row with its ordered options.

    Base ``Question.pk`` / ``QuestionOption.pk`` are NOT exposed -- consumers
    key on ``prompt`` / ``label`` + ``order`` (matching how the onboarding
    re-route restores answers by label/prompt).
    """
    return {
        "prompt": question.prompt,
        "question_type": question.question_type,
        "help_text": question.help_text,
        "is_required": question.is_required,
        "order": question.order,
        "scale_min": question.scale_min,
        "scale_max": question.scale_max,
        "options": [serialize_question_option(o) for o in question.options.all()],
    }


def serialize_questionnaire(questionnaire):
    """One ``Questionnaire`` with nested ordered base questions + options."""
    questions = list(questionnaire.questions.all())
    return {
        "slug": questionnaire.slug,
        "title": questionnaire.title,
        "purpose": questionnaire.purpose,
        "description": questionnaire.description,
        "is_active": questionnaire.is_active,
        "question_count": len(questions),
        "questions": [serialize_question(q) for q in questions],
    }


def serialize_persona(persona):
    """One ``Persona`` row.

    ``default_questionnaire`` is the linked questionnaire ``slug`` or
    ``null`` when the persona has none.
    """
    linked = persona.default_questionnaire
    return {
        "slug": persona.slug,
        "name": persona.name,
        "archetype": persona.archetype,
        "description": persona.description,
        "is_active": persona.is_active,
        "order": persona.order,
        "default_questionnaire": linked.slug if linked is not None else None,
    }


# ---- B. Member responses ---------------------------------------------------


def _normalize_answer(response_question, answer):
    """Normalize one ``ResponseQuestion`` + its ``Answer`` by type.

    Reads the SNAPSHOT (``answer`` is an ``Answer`` row or ``None``;
    selected labels come from ``Answer.selected_options`` /
    ``ResponseQuestionOption.label``, never base ``QuestionOption``).

    - ``text`` / ``long_text`` -> the text string, ``null`` when blank.
    - ``scale`` / ``number``   -> the integer, ``null`` when not answered.
    - ``single_choice``        -> one label string, ``null`` when none.
    - ``multiple_choice``      -> ordered list of labels, ``[]`` when none.

    A ``ResponseQuestion`` with no ``Answer`` row yields the type's empty
    value (``null`` for text/number/single_choice, ``[]`` for
    multiple_choice), so unanswered questions are always represented.
    """
    qtype = response_question.question_type

    if qtype in _TEXT_TYPES:
        if answer is None:
            return None
        text = (answer.text_value or "").strip()
        return text or None

    if qtype in _NUMBER_TYPES:
        if answer is None:
            return None
        return answer.number_value

    if qtype == _MULTIPLE_CHOICE:
        if answer is None:
            return []
        # ``selected_options`` is ordered by ``ResponseQuestionOption``
        # Meta.ordering (``order, id``).
        return [opt.label for opt in answer.selected_options.all()]

    if qtype == _SINGLE_CHOICE:
        if answer is None:
            return None
        labels = [opt.label for opt in answer.selected_options.all()]
        return labels[0] if labels else None

    # Unknown type: fall back to the raw stored text so the feed never
    # silently drops a question. (Defensive -- the model enum is closed.)
    if answer is None:
        return None
    return (answer.text_value or "").strip() or None


def serialize_response(response, *, persona):
    """The shared response-object shape for B1 (per-member) and B2 (bulk).

    Answers are read from the SNAPSHOT rows
    (``ResponseQuestion`` / ``Answer`` / ``ResponseQuestionOption``), never
    the base ``Question``. ``persona`` is the already-resolved persona ORM
    row (or ``None``); resolution lives in the view so the bulk path can
    batch it.

    ``questions`` is ordered by ``ResponseQuestion.order, id`` (model
    Meta.ordering), one item per ``ResponseQuestion`` row of the response,
    including any per-respondent custom question.
    """
    answers_by_question = {
        answer.question_id: answer
        for answer in response.answers.prefetch_related("selected_options").all()
    }

    questions = []
    for rq in response.response_questions.all():
        answer = answers_by_question.get(rq.pk)
        questions.append(
            {
                "prompt": rq.prompt,
                "question_type": rq.question_type,
                "order": rq.order,
                "answer": _normalize_answer(rq, answer),
            }
        )

    persona_payload = None
    if persona is not None:
        persona_payload = {
            "slug": persona.slug,
            "name": persona.name,
            "archetype": persona.archetype,
        }

    return {
        "email": response.respondent.email,
        "questionnaire_slug": response.questionnaire.slug,
        "status": response.status,
        "submitted_at": _isoformat_or_none(response.submitted_at),
        "persona": persona_payload,
        "questions": questions,
    }
