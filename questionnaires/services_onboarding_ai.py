"""Django-facing glue for the AI onboarding interview (issue #804).

The pure interview logic lives in :mod:`questionnaires.onboarding_ai`
(no ORM, no request). This module is the ONLY onboarding-AI code that
touches the database: it builds the persona catalog from ``Persona`` /
``Questionnaire`` rows, persists the chat transcript on an
``OnboardingConversation``, and -- on completion -- materializes the
target onboarding questionnaire onto the member's ``Response`` and writes
the extracted answers as standard #800 ``Answer`` rows (exactly what
#802's form produces).
"""

from questionnaires.models import (
    Answer,
    OnboardingConversation,
    Persona,
    Response,
    ResponseQuestionOption,
)
from questionnaires.onboarding import (
    get_generic_onboarding_questionnaire,
)
from questionnaires.onboarding_ai import (
    OnboardingTurnResult,
    PersonaInfo,
    PersonaQuestion,
    run_onboarding_turn,
    stream_onboarding_turn,
)
from questionnaires.services import build_response_questions

# Persona signals that always route to the generic onboarding
# questionnaire (mirrors #802's none/multiple fallback rule).
_GENERIC_SIGNALS = frozenset({'blend', 'other'})

_TEXT_TYPES = frozenset({'text', 'long_text'})
_NUMBER_TYPES = frozenset({'scale', 'number'})
_CHOICE_TYPES = frozenset({'single_choice', 'multiple_choice'})


def build_persona_catalog():
    """Build the ORM-free persona catalog the core callable consumes.

    One :class:`PersonaInfo` per active persona that has a
    ``default_questionnaire``, carrying the member-safe archetype +
    description + question spine. The internal persona ``name`` is
    deliberately excluded; the persona ``slug`` is reused as the routing
    ``signal`` (it matches the ``PersonaSignal`` enum values
    alex/priya/sam/taylor).
    """
    catalog = []
    personas = (
        Persona.objects
        .filter(is_active=True, default_questionnaire__isnull=False)
        .select_related('default_questionnaire')
        .prefetch_related('default_questionnaire__questions__options')
        .order_by('order', 'name')
    )
    for persona in personas:
        questions = [
            PersonaQuestion(
                prompt=q.prompt,
                question_type=q.question_type,
                options=[opt.label for opt in q.options.all()],
            )
            for q in persona.default_questionnaire.questions.all()
        ]
        catalog.append(PersonaInfo(
            signal=persona.slug,
            archetype=persona.archetype,
            description=persona.description,
            questions=questions,
        ))
    return catalog


def get_or_create_conversation(response):
    """Return the member's ``OnboardingConversation`` row, creating it."""
    conversation, _created = OnboardingConversation.objects.get_or_create(
        response=response,
    )
    return conversation


def resolve_target_questionnaire_for_signal(persona_signal):
    """Map an inferred ``persona_signal`` to the target questionnaire.

    Same fallback rule as #802: ``blend`` / ``other`` (or any unknown
    signal, or a persona without a ``default_questionnaire``) routes to
    the generic onboarding questionnaire. A recognised persona slug
    routes to that persona's ``default_questionnaire``.
    """
    generic = get_generic_onboarding_questionnaire()
    signal = (persona_signal or '').strip().lower()
    if signal in _GENERIC_SIGNALS or not signal:
        return generic
    persona = (
        Persona.objects
        .filter(slug=signal, is_active=True)
        .select_related('default_questionnaire')
        .first()
    )
    if persona is not None and persona.default_questionnaire is not None:
        return persona.default_questionnaire
    return generic


def _write_extracted_answers(response, extracted_answers):
    """Write the extracted answers as #800 ``Answer`` rows.

    Matches each :class:`ExtractedAnswer` against the response's
    materialized ``ResponseQuestion`` rows by prompt, then stores the
    value by question type (choice -> ``selected_options``, scale/number
    -> ``number_value``, text/long_text -> ``text_value``). Unmatched
    extracted answers are skipped so a prompt drift never raises.
    """
    rqs_by_prompt = {
        rq.prompt: rq
        for rq in response.response_questions.prefetch_related('options').all()
    }
    for extracted in extracted_answers or []:
        rq = rqs_by_prompt.get(extracted.prompt)
        if rq is None:
            continue
        answer, _ = Answer.objects.get_or_create(response=response, question=rq)
        qtype = rq.question_type
        if qtype in _NUMBER_TYPES:
            answer.text_value = ''
            answer.number_value = extracted.number_value
            answer.save(update_fields=['text_value', 'number_value', 'updated_at'])
            answer.selected_options.clear()
        elif qtype in _CHOICE_TYPES:
            answer.text_value = ''
            answer.number_value = None
            answer.save(update_fields=['text_value', 'number_value', 'updated_at'])
            wanted = {label.strip().lower() for label in extracted.selected_labels}
            matched = [
                opt for opt in rq.options.all()
                if opt.label.strip().lower() in wanted
            ]
            if matched:
                answer.selected_options.set(
                    ResponseQuestionOption.objects.filter(
                        pk__in=[o.pk for o in matched],
                    ),
                )
            else:
                answer.selected_options.clear()
        else:  # text / long_text (and any unknown type as text)
            answer.number_value = None
            answer.text_value = extracted.text_value or ''
            answer.save(update_fields=['text_value', 'number_value', 'updated_at'])
            answer.selected_options.clear()


def finalize_conversation(conversation, turn_result):
    """Persist a completed interview as #800 ``Response`` / ``Answer`` rows.

    Routes the inferred ``persona_signal`` to a target onboarding
    questionnaire (persona default or the generic fallback), repoints the
    member's onboarding ``Response`` at it if needed, materializes its
    question set via ``build_response_questions``, writes the extracted
    answers, stamps the internal signal Studio-side, and
    ``mark_submitted()``s the response. Returns the submitted response.
    """
    response = conversation.response
    extraction = turn_result.extraction
    persona_signal = extraction.persona_signal.value if extraction else ''

    target = resolve_target_questionnaire_for_signal(persona_signal)
    if target is not None and response.questionnaire_id != target.pk:
        # The member entered chat against the generic placeholder; repoint
        # to the inferred target and re-materialize its question set.
        # Safe because a draft AI response carries no member-entered
        # answers yet (the chat transcript is the only state).
        response.response_questions.all().delete()
        response.questionnaire = target
        response.save(update_fields=['questionnaire', 'updated_at'])

    build_response_questions(response)
    _write_extracted_answers(response, turn_result.answers)

    conversation.persona_signal = persona_signal
    conversation.save(update_fields=['persona_signal', 'updated_at'])

    response.mark_submitted()
    # Notify staff exactly like the #802 form path (issue #882). Imported
    # here (not at module top) to avoid a questionnaires -> crm -> plans
    # import cycle at app-load time; the notifier is best-effort and never
    # breaks the submission.
    from crm.services.onboarding_notify import (  # noqa: PLC0415
        notify_staff_onboarding_submitted,
    )
    notify_staff_onboarding_submitted(response.respondent)
    return response


def run_member_turn(conversation, member_message, *, persona_catalog=None):
    """Run one member turn: call the core, persist the transcript.

    Loads the persisted transcript, calls the pure
    :func:`run_onboarding_turn`, appends both the member message (when
    present) and the assistant reply to the transcript, and -- when the
    interview completes -- finalizes the onboarding response. Returns the
    :class:`OnboardingTurnResult`.

    Any :class:`~integrations.services.llm.LLMError` propagates to the
    caller (the view), which routes the member to the #802 form fallback.
    """
    if persona_catalog is None:
        persona_catalog = build_persona_catalog()
    transcript = conversation.transcript if isinstance(
        conversation.transcript, list,
    ) else []

    result = run_onboarding_turn(
        transcript,
        member_message=member_message,
        persona_catalog=persona_catalog,
    )

    if member_message is not None:
        conversation.append_turn('user', member_message)
    conversation.append_turn('assistant', result.assistant_message)
    conversation.save(update_fields=['transcript', 'updated_at'])

    if result.is_complete:
        finalize_conversation(conversation, result)

    return result


def stream_member_turn(conversation, member_message, *, persona_catalog=None):
    """Stream one member turn: yield text deltas, then persist the turn.

    Streaming counterpart to :func:`run_member_turn` (issue #806). It is a
    generator: it yields incremental ``str`` text deltas as the assistant
    reply is produced, and finally yields the authoritative
    :class:`OnboardingTurnResult` (the LAST item).

    Persistence is IDENTICAL to :func:`run_member_turn` and happens only
    AFTER the authoritative result is assembled: the member message + the
    assistant reply are appended to the transcript, and on completion the
    response is finalized into the SAME #800 ``Response`` / ``Answer``
    rows. Because nothing is written until the stream completes, a
    mid-stream failure (which raises :class:`LLMError` before any write)
    leaves no partial state — so a retry via the v1 non-streaming endpoint
    is the first and only write and cannot create a duplicate turn or
    duplicate answers.

    Any :class:`~integrations.services.llm.LLMError` propagates to the
    caller (the streaming view), which signals the client to fall back.
    """
    if persona_catalog is None:
        persona_catalog = build_persona_catalog()
    transcript = conversation.transcript if isinstance(
        conversation.transcript, list,
    ) else []

    result = None
    for item in stream_onboarding_turn(
        transcript,
        member_message=member_message,
        persona_catalog=persona_catalog,
    ):
        if isinstance(item, OnboardingTurnResult):
            result = item
        else:
            yield item

    # Persist only after the authoritative result is in hand (no partial
    # writes on a mid-stream failure -> no duplicate on a v1 retry).
    if member_message is not None:
        conversation.append_turn('user', member_message)
    conversation.append_turn('assistant', result.assistant_message)
    conversation.save(update_fields=['transcript', 'updated_at'])

    if result.is_complete:
        finalize_conversation(conversation, result)

    yield result


def get_or_create_ai_onboarding_response(user):
    """Return the member's onboarding ``Response`` for the AI chat path.

    Reuses any existing onboarding response (so chat and form share one
    response per member). When the member has none, creates a draft
    against the generic onboarding questionnaire as a placeholder; the
    final questionnaire is resolved from the inferred persona signal at
    completion. Returns ``(response, conversation)`` or ``(None, None)``
    when no onboarding questionnaire is seeded at all.
    """
    existing = (
        Response.objects
        .filter(respondent=user, questionnaire__purpose='onboarding')
        .select_related('questionnaire')
        .order_by('created_at')
        .first()
    )
    if existing is not None:
        return existing, get_or_create_conversation(existing)

    generic = get_generic_onboarding_questionnaire()
    if generic is None:
        return None, None
    response = Response.objects.create(
        questionnaire=generic,
        respondent=user,
        status='draft',
    )
    # Materialize the generic questions immediately so the "switch to the
    # form" fallback link works for an in-progress AI response (the
    # questionnaire is repointed + re-materialized at completion if the
    # inferred persona differs).
    build_response_questions(response)
    return response, get_or_create_conversation(response)
