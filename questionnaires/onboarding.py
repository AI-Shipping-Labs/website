"""Form-first member onboarding helpers (issue #802).

The onboarding flow lives in the ``accounts`` app (its views), but the
domain logic that maps the member's self-identification selection to a
target onboarding ``Questionnaire`` — and the derived "has this member
onboarded?" state — belongs with the questionnaire models, so it is kept
here next to the shared fill-in services.

Design notes:
- Onboarding completion is NOT a model field. It is derived from the
  existence of a submitted ``purpose='onboarding'`` ``Response`` for the
  member, so no migration is needed.
- The member-facing self-identification options are ARCHETYPE
  descriptions only. The internal persona name (Alex / Priya / Sam /
  Taylor) MUST never reach the member, so each option carries the
  persona ``id`` as an opaque value and is labeled with the archetype.
"""

from content.access import LEVEL_BASIC, get_user_level
from integrations.config import get_config
from integrations.services import llm
from questionnaires.models import (
    Answer,
    Persona,
    Questionnaire,
    Response,
)
from questionnaires.services import build_response_questions

_CHOICE_TYPES = frozenset({'single_choice', 'multiple_choice'})
# Answer types whose value lives in ``Answer.text_value``.
_TEXT_TYPES = frozenset({'text', 'long_text'})
# Answer types whose value lives in ``Answer.number_value``.
_NUMBER_TYPES = frozenset({'scale', 'number'})
_MULTIPLE_CHOICE = 'multiple_choice'
_SINGLE_CHOICE = 'single_choice'

# Slug of the persona-agnostic onboarding questionnaire seeded by #801.
GENERIC_ONBOARDING_SLUG = 'onboarding-general'

# Config flag (Studio/.env) gating the conversational AI onboarding path
# on top of the LLM service being enabled. Defaults on when the LLM is
# enabled; switchable without a redeploy via Studio. Registered in
# ``integrations.settings_registry`` so it is Studio-configurable.
ONBOARDING_AI_FLAG = 'ONBOARDING_AI_ENABLED'

# Config flag (Studio/.env) gating token-by-token SSE streaming of the AI
# onboarding chat reply on top of the AI path being available (#806).
# Defaults on when the AI path is on; when off the chat uses the v1
# non-streaming transport and never opens an SSE connection. Switchable
# without a redeploy via Studio.
ONBOARDING_AI_STREAMING_FLAG = 'ONBOARDING_AI_STREAMING'

# Opaque self-ID values for the two persona-agnostic options. They are
# not persona ids, so they never collide with a ``Persona.pk`` value.
SELF_ID_NONE = 'none'
SELF_ID_MULTIPLE = 'multiple'
_GENERIC_VALUES = frozenset({SELF_ID_NONE, SELF_ID_MULTIPLE})


def ai_onboarding_available():
    """True when the conversational AI onboarding path should be offered.

    Requires BOTH the LLM service to be enabled (#799 ``is_enabled()``)
    AND the ``ONBOARDING_AI_ENABLED`` config flag to be on (default true).
    When either is off, ``/onboarding/`` renders #802's form unchanged.
    """
    if not llm.is_enabled():
        return False
    # Default ON when unset: the flag exists to turn the AI path OFF
    # without disabling the whole LLM service. Only an explicit falsey
    # value disables it.
    raw = get_config(ONBOARDING_AI_FLAG, 'true')
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in ('true', '1', 'yes')


def ai_onboarding_streaming_enabled():
    """True when the onboarding chat should stream replies over SSE (#806).

    Requires the AI onboarding path to be available AND the
    ``ONBOARDING_AI_STREAMING`` flag to be on (default true). When off (or
    the AI path is unavailable), the chat uses the v1 non-streaming
    transport and opens no SSE connection.
    """
    if not ai_onboarding_available():
        return False
    raw = get_config(ONBOARDING_AI_STREAMING_FLAG, 'true')
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in ('true', '1', 'yes')


def can_access_onboarding(user):
    """True when ``user`` may enter the onboarding flow (issue #982).

    Onboarding feeds the personalized plan and the 1:1 founder call, both
    paid-member benefits. Access is gated to an effective tier level of
    ``LEVEL_BASIC`` (10) or higher, resolved via the canonical
    override-aware :func:`content.access.get_user_level` — so an active
    (non-expired) ``TierOverride`` raising the effective tier counts as
    paid, while a Free base tier with no active override does not.
    Anonymous users resolve to level 0 and are denied; staff / superusers
    resolve to ``LEVEL_PREMIUM`` and keep access.

    This is the SINGLE shared predicate backing every onboarding surface
    (dashboard prompt, ``/onboarding/...`` views, AI chat, request-a-call
    CTA). Never read ``user.tier.level`` directly at a call site.
    """
    return get_user_level(user) >= LEVEL_BASIC


def has_completed_onboarding(user):
    """True when ``user`` has a submitted onboarding ``Response``."""
    if not user.is_authenticated:
        return False
    return Response.objects.filter(
        respondent=user,
        questionnaire__purpose='onboarding',
        status='submitted',
    ).exists()


def get_onboarding_response(user):
    """Return the member's onboarding ``Response`` (draft or submitted).

    There is at most one because the self-ID step is asked only once per
    submission. While the response is still a DRAFT the member may return
    to self-identification and re-pick a persona (#822) via
    :func:`reroute_onboarding_response`; once SUBMITTED, switching persona
    is a staff action. Returns ``None`` when the member has not started
    onboarding.
    """
    return (
        Response.objects
        .filter(respondent=user, questionnaire__purpose='onboarding')
        .select_related('questionnaire')
        .order_by('created_at')
        .first()
    )


def get_generic_onboarding_questionnaire():
    """Return the seeded generic onboarding questionnaire, or ``None``."""
    return (
        Questionnaire.objects
        .filter(slug=GENERIC_ONBOARDING_SLUG, purpose='onboarding')
        .first()
    )


def self_identification_options():
    """Build the member-facing self-identification option list.

    One option per active ``Persona`` that has a ``default_questionnaire``,
    plus the two persona-agnostic options. Each persona option's ``value``
    is the persona pk (opaque) and its ``label`` is the persona archetype
    — the internal persona ``name`` is deliberately excluded.
    """
    options = []
    personas = (
        Persona.objects
        .filter(is_active=True, default_questionnaire__isnull=False)
        .order_by('order', 'name')
    )
    for persona in personas:
        options.append({
            'value': str(persona.pk),
            'label': persona.archetype,
            'help_text': persona.description,
        })
    options.append({
        'value': SELF_ID_NONE,
        'label': 'None of these / not sure',
        'help_text': '',
    })
    options.append({
        'value': SELF_ID_MULTIPLE,
        'label': 'More than one / both',
        'help_text': '',
    })
    return options


def resolve_target_questionnaire(selection):
    """Map a self-ID selection to its target onboarding ``Questionnaire``.

    - ``none`` / ``multiple`` -> the generic onboarding questionnaire.
    - a persona pk -> that persona's ``default_questionnaire``, falling
      back to the generic questionnaire when the persona has none (data
      gap) or the selection does not match an active persona.

    Returns ``None`` when no onboarding questionnaire is available at all
    (the caller shows a friendly "not ready yet" message rather than 500).
    """
    generic = get_generic_onboarding_questionnaire()
    if selection in _GENERIC_VALUES:
        return generic

    if selection and selection.isdigit():
        persona = (
            Persona.objects
            .filter(pk=int(selection), is_active=True)
            .select_related('default_questionnaire')
            .first()
        )
        if persona is not None and persona.default_questionnaire is not None:
            return persona.default_questionnaire

    # Unknown selection or persona without a questionnaire: fall back.
    return generic


def _snapshot_answers_by_prompt(response):
    """Capture a draft response's current answers keyed by question prompt.

    Returns a dict ``prompt -> {'type', 'text', 'number', 'labels'}`` so the
    answer can be re-attached to a same-prompt question after the question
    set is rebuilt for a different persona. Choice answers carry option
    LABELS (not ids), because the new questionnaire snapshots fresh option
    rows with new ids; matching by label re-selects the equivalent options.
    """
    snapshot = {}
    answers = (
        response.answers
        .select_related('question')
        .prefetch_related('selected_options')
    )
    for answer in answers:
        rq = answer.question
        if rq.question_type in _CHOICE_TYPES:
            labels = [opt.label for opt in answer.selected_options.all()]
            # An empty choice answer carries no information to preserve.
            if not labels:
                continue
            snapshot[rq.prompt] = {
                'type': rq.question_type,
                'labels': labels,
            }
        elif answer.text_value:
            snapshot[rq.prompt] = {
                'type': rq.question_type,
                'text': answer.text_value,
            }
        elif answer.number_value is not None:
            snapshot[rq.prompt] = {
                'type': rq.question_type,
                'number': answer.number_value,
            }
    return snapshot


def _restore_answers_by_prompt(response, snapshot):
    """Re-attach preserved answers to the response's new question set.

    Only questions whose prompt is in ``snapshot`` get an answer; new delta
    questions stay unanswered. Choice answers re-select the new option rows
    whose label matches a preserved label; a label with no counterpart in
    the new question is simply dropped (no orphan, no error).
    """
    for rq in response.response_questions.prefetch_related('options').all():
        saved = snapshot.get(rq.prompt)
        if saved is None:
            continue
        if rq.question_type in _CHOICE_TYPES:
            # Only restore between matching choice types; a prompt that
            # flipped type across questionnaires would not be a safe restore.
            if saved.get('type') not in _CHOICE_TYPES:
                continue
            matching = [
                opt for opt in rq.options.all() if opt.label in saved['labels']
            ]
            if not matching:
                continue
            answer = Answer.objects.create(response=response, question=rq)
            answer.selected_options.set(matching)
        elif 'text' in saved:
            Answer.objects.create(
                response=response, question=rq, text_value=saved['text'],
            )
        elif 'number' in saved:
            Answer.objects.create(
                response=response, question=rq, number_value=saved['number'],
            )


def reroute_onboarding_response(response, target):
    """Repoint a DRAFT onboarding response to ``target`` questionnaire (#822).

    A member who picked the wrong persona at self-identification may return
    while their response is still a draft and choose a different one. The
    question set differs per persona, so this:

    1. Snapshots the member's current answers keyed by question prompt.
    2. Deletes the old ``ResponseQuestion`` rows (cascading their answers).
    3. Repoints the response at ``target`` and re-materializes its full
       question set via :func:`build_response_questions`.
    4. Restores answers to any question whose prompt is shared (the common
       spine), matching choice options by label. Answers to delta questions
       absent from ``target`` are dropped — never silently kept as orphans.

    No-op when ``target`` is ``None`` or already the current questionnaire
    (besides ensuring the question set is materialized). Returns ``response``.
    """
    if target is None:
        return response
    if response.questionnaire_id == target.pk:
        # Same persona re-picked: just make sure questions are materialized.
        build_response_questions(response)
        return response

    snapshot = _snapshot_answers_by_prompt(response)
    response.response_questions.all().delete()
    response.questionnaire = target
    response.save(update_fields=['questionnaire', 'updated_at'])
    build_response_questions(response)
    _restore_answers_by_prompt(response, snapshot)
    return response


def normalize_answer(response_question, answer):
    """Normalize one ``ResponseQuestion`` + its ``Answer`` row by type.

    The single source of answer-type branching shared by the read-only
    onboarding API (``api/serializers/onboarding.serialize_response``) and
    the Studio CRM detail page (``flatten_response_answers``). Reads the
    SNAPSHOT layer only (the ``answer`` is an ``Answer`` row or ``None``;
    choice labels come from ``Answer.selected_options`` /
    ``ResponseQuestionOption.label``, never the base ``QuestionOption``).

    Returns, by question type:

    - ``text`` / ``long_text`` -> the text string, or ``None`` when blank.
    - ``scale`` / ``number``   -> the integer, or ``None`` when unanswered.
    - ``single_choice``        -> one label string, or ``None`` when none.
    - ``multiple_choice``      -> an ordered list of labels, ``[]`` when none.

    An unanswered question (no ``Answer`` row) yields the type's empty value
    so unanswered questions are still represented.
    """
    qtype = response_question.question_type

    if qtype in _TEXT_TYPES:
        if answer is None:
            return None
        return (answer.text_value or '').strip() or None

    if qtype in _NUMBER_TYPES:
        if answer is None:
            return None
        return answer.number_value

    if qtype == _MULTIPLE_CHOICE:
        if answer is None:
            return []
        return [opt.label for opt in answer.selected_options.all()]

    if qtype == _SINGLE_CHOICE:
        if answer is None:
            return None
        labels = [opt.label for opt in answer.selected_options.all()]
        return labels[0] if labels else None

    # Unknown type (defensive -- the model enum is closed): fall back to the
    # raw stored text so a question is never silently dropped.
    if answer is None:
        return None
    return (answer.text_value or '').strip() or None


def _display_value(normalized):
    """Render a normalized answer as a human-readable string.

    Mirrors ``Answer.display_value`` (joins multi-choice labels with
    ``', '``) but works off the already-normalized value so it shares the
    single answer-type branch in :func:`normalize_answer`. Returns ``''``
    for an empty/unanswered value so callers can render an explicit blank.
    """
    if normalized is None:
        return ''
    if isinstance(normalized, list):
        return ', '.join(normalized)
    if isinstance(normalized, bool):
        # Defensive: booleans aren't a question type, but ``str(True)`` is
        # never what we want to show.
        return ''
    return str(normalized)


def flatten_response_answers(response):
    """Return an ordered flat Q&A list for a member's onboarding response.

    One item per ``ResponseQuestion`` (ordered by the model's
    ``order, id``) as a dict with:

    - ``prompt``: the snapshot question prompt.
    - ``question_type``: the snapshot question type.
    - ``order``: the snapshot order.
    - ``value``: the normalized answer (string / int / list / ``None``).
    - ``display``: the human-readable string for the CRM template.
    - ``answered``: ``True`` when the member supplied an answer.

    Reuses :func:`normalize_answer` so the CRM page and the read-only API
    share one answer-type branch. Reads the SNAPSHOT rows only.
    """
    answers_by_question = {
        answer.question_id: answer
        for answer in (
            response.answers.prefetch_related('selected_options').all()
        )
    }

    rows = []
    for rq in response.response_questions.all():
        answer = answers_by_question.get(rq.pk)
        value = normalize_answer(rq, answer)
        display = _display_value(value)
        rows.append({
            'prompt': rq.prompt,
            'question_type': rq.question_type,
            'order': rq.order,
            'value': value,
            'display': display,
            'answered': bool(display),
        })
    return rows
