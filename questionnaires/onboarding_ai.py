"""AI onboarding interviewer core (issue #804).

A pure, Django-independent callable that drives one turn of a
conversational onboarding interview. The view/HTTP layer (and the future
#809 eval runner) is a thin wrapper: it loads the persisted transcript +
persona catalog from the ORM, calls :func:`run_onboarding_turn`, persists
the new turn, and -- on completion -- writes the standard #800
``Response`` / ``Answer`` rows.

Django-independence is a hard contract. This module imports neither
``django.db`` models, nor ``request`` objects, nor the ``accounts`` /
``plans`` apps. Its only dependencies are the provider-neutral LLM
service from #799 (:mod:`integrations.services.llm`) and Pydantic. An
import-isolation test enforces this so #809 can wrap it without dragging
in the request layer.

Structured output uses the #799 pattern: the
:class:`OnboardingExtraction` Pydantic model doubles as the tool input
schema via ``model_json_schema()``; on the final turn the model returns
its answer as a single tool call whose validated input is the
extraction.

Persona names (Alex / Priya / Sam / Taylor) are INTERNAL. The system
prompt instructs the assistant to reason in archetype terms only and
never surface a persona name to the member; the ``persona_signal`` field
is the internal signal stored staff-side, never echoed to the member.
"""

import re
import time
from datetime import date
from enum import Enum

from pydantic import BaseModel, Field

from integrations.services import llm
from integrations.services.llm import LLMError

# The system prompt is versioned via this module-level constant so #809
# can diff prompt revisions across stored runs.
SYSTEM_PROMPT = (
    'You are a warm, focused onboarding interviewer for a hands-on AI '
    'engineering cohort program. Your job is to interview a new member in a '
    'short conversation so the team can build them a personalised 6-8 week '
    'plan.\n\n'
    'Interview guidance:\n'
    '- Infer the member\'s archetype from their engineering comfort, their '
    'AI/ML comfort, and whether they have shipped/deployed a project -- not '
    'from self-classification alone. Roughly a third of members are blends; '
    'do not force a single label.\n'
    '- Ask about accountability needs and what blocks their consistency '
    'early and concretely -- these are the most load-bearing answers.\n'
    '- Push the member toward ONE concrete project with an explicit '
    'in-scope and out-of-scope; if they have several ideas, help them pick '
    'one and defer the rest.\n'
    '- Capture how their weekly time behaves (steady vs variable vs '
    'front-loaded), not just an average number of hours.\n'
    '- Clarify how they want to use coding agents: some want a low-effort '
    'agent fallback, others explicitly want to learn without treating AI as '
    'a black box. Do not assume.\n'
    '- Surface evaluation as a likely depth gap for members who already '
    'ship AI or come from research.\n'
    '- Ask one focused question at a time; acknowledge the previous answer '
    'briefly before moving on. Keep the whole interview to a handful of '
    'turns.\n\n'
    'Tailor the interview to ONE working archetype:\n'
    '- After the first couple of answers (enough to read engineering '
    'comfort, AI/ML comfort, and shipped/deployed status), COMMIT to a '
    'single working archetype from the catalog below and treat it as your '
    'hypothesis for the rest of the interview. You may revise it if later '
    'answers clearly contradict it, but do not keep all archetypes open.\n'
    '- Once committed, PRIORITISE that archetype\'s delta questions (the '
    'questions listed only under that archetype) and draw your follow-ups '
    'from them. Do NOT ask the other archetypes\' delta questions -- those '
    'belong to different members.\n'
    '- ALWAYS still ask the shared-spine questions (listed once below under '
    '"Shared spine"); they apply to every member regardless of archetype.\n'
    '- If the member is a genuine blend or none of the archetypes fit, fall '
    'back to the shared spine plus the most relevant deltas, and record the '
    'signal as "blend"/"other" at the end.\n\n'
    'Hard rules:\n'
    '- Speak in plain archetype descriptions. NEVER mention or imply any '
    'internal persona codename (the names Alex, Priya, Sam, Taylor are '
    'internal and must never appear in anything you say to the member).\n'
    '- When you have gathered enough to populate a plan, tell the member '
    'the interview is complete in a friendly sentence, and call the '
    '"' + 'record_onboarding' + '" tool with the structured extraction. '
    'Do not call the tool before the interview is complete.'
)

# Name of the structured-output tool the model calls on the final turn.
_TOOL_NAME = 'record_onboarding'

# The opening line shown when the member has not sent any message yet. It
# is deterministic (no model call) so the chat surface always greets the
# member instantly and the greeting never leaks a persona name.
GREETING = (
    "Hi! I'd love to learn a bit about you so we can build the right plan. "
    "To start: what is the one concrete thing you'd like to have built or "
    "achieved by the end of the next 6 to 8 weeks?"
)


# --- Enums for the extraction schema (the appendix) ---


class PersonaSignal(str, Enum):
    ALEX = 'alex'
    PRIYA = 'priya'
    SAM = 'sam'
    TAYLOR = 'taylor'
    BLEND = 'blend'
    OTHER = 'other'


class GoalCategory(str, Enum):
    SHIP_NEW = 'ship_new'
    IMPROVE_EXISTING = 'improve_existing'
    STRENGTHEN_ENG = 'strengthen_eng'
    BUILD_FOUNDATIONS = 'build_foundations'
    CAREER_PORTFOLIO = 'career_portfolio'


class TimeProfile(str, Enum):
    STEADY = 'steady'
    VARIABLE = 'variable'
    FRONT_LOADED = 'front_loaded'


class MainBlocker(str, Enum):
    SCOPING = 'scoping'
    STARTING = 'starting'
    MOMENTUM = 'momentum'
    FINISHING = 'finishing'
    TECHNICAL = 'technical'
    FOMO = 'fomo'
    TIME = 'time'
    NO_FEEDBACK = 'no_feedback'
    PERFECTIONISM = 'perfectionism'


class ProjectStage(str, Enum):
    NONE = 'none'
    IDEA = 'idea'
    SCOPED = 'scoped'
    STARTED = 'started'
    BUILT_LOCAL = 'built_local'
    DEPLOYED_NEEDS_HARDENING = 'deployed_needs_hardening'


class CareerDirection(str, Enum):
    AI_ENGINEER = 'ai_engineer'
    AI_PLATFORM_MLOPS = 'ai_platform_mlops'
    APPLIED_AI = 'applied_ai'
    RESEARCH_ENGINEER = 'research_engineer'
    DATA_ENG = 'data_eng'
    UNDECIDED = 'undecided'
    NOT_CAREER_FOCUSED = 'not_career_focused'


class CodingAgentUse(str, Enum):
    HEAVY = 'heavy'
    WANTS_TO_LEARN_WITHOUT = 'wants_to_learn_without'
    BOILERPLATE_ONLY = 'boilerplate_only'
    UNDECIDED = 'undecided'


class PlanHorizon(str, Enum):
    SINGLE_SPRINT = 'single_sprint'
    PHASED_TWO_SPRINTS = 'phased_two_sprints'
    LONG_ARC = 'long_arc'


class OnboardingExtraction(BaseModel):
    """Structured intake the assistant fills on the final interview turn.

    Mirrors the issue's extraction-schema appendix exactly: every field,
    its type, the documented enums, and the nullable fields. Its
    ``model_json_schema()`` is the #799 structured-output tool input
    schema, and validating ``result.tool_input`` against this model is
    what guarantees the callable returns this shape.

    ``persona_signal`` is the INTERNAL archetype signal. It is stored
    staff-side and never echoed to the member.
    """

    persona_signal: PersonaSignal = Field(
        description=(
            'Internal archetype signal inferred from eng/ai comfort + deploy '
            'status. Never shown to the member.'
        ),
    )
    eng_comfort: int = Field(
        ge=1, le=5, description='Software-engineering comfort, 1-5.',
    )
    ai_comfort: int = Field(
        ge=1, le=5, description='AI/ML concept comfort, 1-5.',
    )
    primary_goal: str = Field(
        description='The one concrete 6-8 week outcome the member wants.',
    )
    goal_category: GoalCategory = Field(
        description='Which category the primary goal falls into.',
    )
    time_commitment_hours_per_week: int = Field(
        ge=0, description='Average hours per week the member can commit.',
    )
    time_profile: TimeProfile = Field(
        description='How the weekly time behaves over the plan.',
    )
    main_blocker: MainBlocker = Field(
        description='The single biggest thing that blocks consistency.',
    )
    secondary_blockers: list[str] = Field(
        default_factory=list, description='Other blockers mentioned.',
    )
    accountability_preference: list[str] = Field(
        default_factory=list,
        description='Kinds of accountability that help this member.',
    )
    current_project: str | None = Field(
        default=None,
        description='Rough current project/idea, or null if none.',
    )
    project_stage: ProjectStage = Field(
        description='How far along the project is.',
    )
    target_outcome: str = Field(
        description='What "done / worthwhile" looks like for the member.',
    )
    career_direction: CareerDirection = Field(
        description='Stated or inferred career direction.',
    )
    tech_stack_known: list[str] = Field(
        default_factory=list,
        description='Technologies the member is already comfortable with.',
    )
    tech_stack_gaps: list[str] = Field(
        default_factory=list,
        description='Technologies the member needs to learn.',
    )
    in_scope: list[str] = Field(
        default_factory=list,
        description='What is in scope for the first version.',
    )
    out_of_scope: list[str] = Field(
        default_factory=list,
        description='What is explicitly deferred / out of scope.',
    )
    coding_agent_use: CodingAgentUse = Field(
        description='How the member wants to use coding agents.',
    )
    support_wanted: list[str] = Field(
        default_factory=list,
        description='Kinds of support the member wants from the team.',
    )
    learning_track_links: list[str] = Field(
        default_factory=list,
        description='Relevant learning track / resource links.',
    )
    hard_deadline: date | None = Field(
        default=None, description='Hard deadline if any, else null.',
    )
    plan_horizon: PlanHorizon = Field(
        description='How long the plan should span.',
    )
    notes: str = Field(
        default='', description='Freeform notes for the planner.',
    )


# --- Plain value objects passed in / out of the core ---


class Message(BaseModel):
    """One provider-neutral chat turn (``role`` is ``user``/``assistant``)."""

    role: str
    content: str


class PersonaQuestion(BaseModel):
    """One question from a persona's spine, as a plain value object."""

    prompt: str
    question_type: str
    options: list[str] = Field(default_factory=list)


class PersonaInfo(BaseModel):
    """Archetype + question spine for one persona, with the NAME kept out.

    The caller builds these from ``Persona`` + ``Questionnaire`` rows. The
    persona's internal ``name`` is deliberately excluded: the core only
    sees the ``signal`` (the enum value, for routing) plus the
    member-safe ``archetype`` / ``description`` / question spine.
    """

    signal: str
    archetype: str
    description: str = ''
    questions: list[PersonaQuestion] = Field(default_factory=list)


class ExtractedAnswer(BaseModel):
    """One extracted answer mapped to a question prompt + type + value.

    The Django layer matches ``prompt`` against the response's
    ``ResponseQuestion`` rows and writes the value into the matching
    ``Answer`` (choice -> ``selected_options``, scale/number ->
    ``number_value``, text/long_text -> ``text_value``).
    """

    prompt: str
    question_type: str
    text_value: str = ''
    number_value: int | None = None
    selected_labels: list[str] = Field(default_factory=list)


class OnboardingTurnResult(BaseModel):
    """The result of one interview turn.

    ``assistant_message`` is what to show the member next. When
    ``is_complete`` is True the interview is finished and ``extraction`` +
    ``answers`` are populated; otherwise both are ``None``.
    """

    assistant_message: str
    is_complete: bool = False
    extraction: OnboardingExtraction | None = None
    answers: list[ExtractedAnswer] | None = None


class TraceSink:
    """No-op trace sink; the default when ``trace`` is omitted.

    The #809 eval runner subclasses this (or passes any object with the
    same methods) to capture each run's system prompt, the messages sent,
    the raw ``LLMResult``, the tool spec / tool input, latency, and the
    parsed output. Every hook defaults to doing nothing so production
    runs stay silent.
    """

    def on_request(self, *, system, messages, tool):
        """Called just before ``llm.complete`` with the rendered request."""

    def on_result(self, *, result, latency_seconds):
        """Called after ``llm.complete`` returns, with the raw result."""

    def on_parsed(self, *, parsed):
        """Called after the tool input validates into an extraction."""

    def on_error(self, *, error):
        """Called when the LLM call or parsing/validation fails."""


# Internal persona names that must never appear in member-facing text.
_INTERNAL_PERSONA_NAMES = ('Alex', 'Priya', 'Sam', 'Taylor')

# Matches a leading article left dangling after a codename is replaced with
# the bare noun phrase "your archetype" (e.g. "a your archetype").
_LEADING_ARTICLE_RE = re.compile(r'\ban?\s+your archetype\b', re.IGNORECASE)


def _shared_spine_prompts(persona_catalog):
    """Return the question prompts shared by EVERY persona, in first-seen order.

    A prompt is "shared spine" when it appears in every persona's question
    spine; everything else is a per-archetype delta. Computed purely from
    the passed catalog (no DB), so the core stays Django-independent. When
    there is only one persona, nothing is treated as shared (there is no
    cross-archetype spine to factor out).
    """
    personas = [p for p in persona_catalog if p.questions]
    if len(personas) < 2:
        return []
    prompt_sets = [{q.prompt for q in p.questions} for p in personas]
    shared = set.intersection(*prompt_sets)
    ordered = []
    for question in personas[0].questions:
        if question.prompt in shared and question.prompt not in ordered:
            ordered.append(question.prompt)
    return ordered


def _format_question(question):
    """Render one question line: ``- (type) prompt options: a, b``."""
    opts = ''
    if question.options:
        opts = f' options: {", ".join(question.options)}'
    return f'  - ({question.question_type}) {question.prompt}{opts}'


def _render_persona_catalog(persona_catalog):
    """Render the archetype + question-spine context for the system prompt.

    The shared spine (prompts common to every persona) is rendered ONCE as
    a labelled "Shared spine" block, and each archetype lists only its
    DELTA questions (the ones unique to that archetype). This lets the
    model branch its follow-ups by the committed archetype's deltas while
    still asking the shared spine for everyone -- the issue #823 goal.

    The internal persona name is never included -- only the routing
    ``signal``, the member-safe ``archetype`` / ``description``, and the
    question prompts/types/options that shape the interview.
    """
    if not persona_catalog:
        return ''
    shared_prompts = _shared_spine_prompts(persona_catalog)
    shared_set = set(shared_prompts)

    lines = []
    if shared_prompts:
        lines.append(
            'Shared spine -- ask these of EVERY member regardless of '
            'archetype:'
        )
        # Render the shared questions from the first persona that carries
        # them, preserving their option lists.
        rendered = set()
        for persona in persona_catalog:
            for question in persona.questions:
                if (
                    question.prompt in shared_set
                    and question.prompt not in rendered
                ):
                    lines.append(_format_question(question))
                    rendered.add(question.prompt)
        lines.append('')

    lines.append(
        'Archetypes to reason about (internal signal in brackets -- never '
        'say it to the member). Once you commit to one archetype, prioritise '
        'ITS delta questions below and skip the others\':'
    )
    for persona in persona_catalog:
        lines.append('')
        lines.append(f'- {persona.archetype} [signal: {persona.signal}]')
        if persona.description:
            lines.append(f'  {persona.description}')
        delta_questions = [
            q for q in persona.questions if q.prompt not in shared_set
        ]
        if delta_questions:
            lines.append('  Delta questions (specific to this archetype):')
            for question in delta_questions:
                lines.append(_format_question(question))
        elif shared_prompts:
            lines.append('  (no archetype-specific delta questions)')
    return '\n'.join(lines)


def _build_system_prompt(persona_catalog):
    """Assemble the full system prompt: base guidance + archetype context."""
    catalog = _render_persona_catalog(persona_catalog)
    if not catalog:
        return SYSTEM_PROMPT
    return f'{SYSTEM_PROMPT}\n\n{catalog}'


def _build_messages(transcript, member_message):
    """Build the provider-neutral message list for the LLM call.

    ``transcript`` is the prior turns (each a plain dict or ``Message``
    with ``role`` / ``content``). ``member_message`` is the member's
    latest line; when it is ``None`` the conversation is being opened, so
    no user message is appended.
    """
    messages = []
    for turn in transcript or []:
        if isinstance(turn, Message):
            role, content = turn.role, turn.content
        else:
            role, content = turn.get('role'), turn.get('content')
        messages.append({'role': role, 'content': content})
    if member_message is not None:
        messages.append({'role': 'user', 'content': member_message})
    return messages


def _sanitize(text):
    """Strip any internal persona name that slipped into model output.

    The system prompt forbids persona names, but this is a hard backstop
    so a stray name never reaches the member. Replaces a leaked name with
    a neutral word.
    """
    cleaned = text or ''
    for name in _INTERNAL_PERSONA_NAMES:
        cleaned = cleaned.replace(name, 'your archetype')
    # Collapse a now-redundant leading article so the substitution reads
    # naturally (e.g. "a Taylor" -> "a your archetype" -> "your archetype").
    cleaned = _LEADING_ARTICLE_RE.sub('your archetype', cleaned)
    return cleaned


def _answers_from_extraction(extraction, persona_catalog):
    """Map a validated extraction onto plain :class:`ExtractedAnswer` rows.

    The Django layer persists these as #800 ``Answer`` rows against the
    target questionnaire's materialized questions. We emit the core,
    cross-persona spine values keyed by the exact base-question prompts so
    the caller can match them by prompt. Anything not matched is ignored
    by the caller, so this never raises on prompt drift.
    """
    answers = []

    def add_text(prompt, value):
        if value:
            answers.append(ExtractedAnswer(
                prompt=prompt, question_type='long_text', text_value=value,
            ))

    def add_number(prompt, value):
        if value is not None:
            answers.append(ExtractedAnswer(
                prompt=prompt, question_type='number', number_value=value,
            ))

    def add_choice(prompt, labels):
        if labels:
            answers.append(ExtractedAnswer(
                prompt=prompt,
                question_type='single_choice',
                selected_labels=list(labels),
            ))

    outcome = extraction.primary_goal or extraction.target_outcome
    add_text(
        'What would you like to have achieved 6 to 8 weeks from now?',
        outcome,
    )
    add_number(
        'How many hours per week can you realistically commit?',
        extraction.time_commitment_hours_per_week,
    )
    add_text(
        'Do you already have a project, idea, or direction in mind?',
        extraction.current_project or '',
    )
    add_text(
        'Anything else we should know before preparing your plan?',
        extraction.notes,
    )
    return answers


def run_onboarding_turn(
    transcript,
    *,
    member_message,
    persona_catalog,
    trace=None,
):
    """Run one turn of the AI onboarding interview.

    Args:
        transcript: Prior turns as a list of ``{'role', 'content'}`` dicts
            (or :class:`Message` instances). Empty/``None`` opens the
            conversation.
        member_message: The member's latest message. ``None`` means open
            the conversation (returns the deterministic greeting without
            an LLM call).
        persona_catalog: A list of :class:`PersonaInfo` value objects
            (archetype + question spine) the caller built from the DB. The
            internal persona name is never part of this object.
        trace: Optional :class:`TraceSink` (or compatible) recording the
            run. ``None`` runs silently.

    Returns:
        OnboardingTurnResult: the assistant's next message and, on the
        final turn, the validated :class:`OnboardingExtraction` plus the
        mapped :class:`ExtractedAnswer` list.

    Raises:
        LLMError: when the LLM call fails or its structured output cannot
            be validated. No partial result is returned.
    """
    sink = trace or TraceSink()

    # Opening turn: greet deterministically, no model call. The greeting
    # is persona-name-free by construction.
    if member_message is None and not transcript:
        return OnboardingTurnResult(
            assistant_message=GREETING, is_complete=False,
        )

    system = _build_system_prompt(persona_catalog)
    messages = _build_messages(transcript, member_message)
    tool = {
        'name': _TOOL_NAME,
        'description': (
            'Record the structured onboarding intake once the interview is '
            'complete.'
        ),
        'input_schema': OnboardingExtraction.model_json_schema(),
    }

    sink.on_request(system=system, messages=messages, tool=tool)

    started = time.monotonic()
    try:
        # ``tool_choice`` is left to ``auto`` (the default) so the model
        # answers conversationally on intermediate turns and only emits
        # the tool call when it judges the interview complete.
        result = llm.complete(
            messages,
            system=system,
            tools=[tool],
        )
    except LLMError as error:
        sink.on_error(error=error)
        raise
    latency_seconds = time.monotonic() - started
    sink.on_result(result=result, latency_seconds=latency_seconds)

    return _turn_result_from_llm(result, persona_catalog, sink)


def _turn_result_from_llm(result, persona_catalog, sink):
    """Build an :class:`OnboardingTurnResult` from one ``LLMResult``.

    Shared by the non-streaming :func:`run_onboarding_turn` and the
    streaming :func:`stream_onboarding_turn` so both derive the
    authoritative turn decision (and the final-turn structured extraction)
    from a SINGLE model generation, byte-for-byte identically.

    Args:
        result: The :class:`LLMResult` from one ``complete``/``stream``
            generation (text plus, on the final turn, ``tool_input``).
        persona_catalog: The persona catalog used to map the extraction.
        sink: The :class:`TraceSink` for ``on_parsed`` / ``on_error``.

    Raises:
        LLMError: when a tool call was returned but its input does not
            validate into an :class:`OnboardingExtraction`.
    """
    # No tool call -> the interview is still in progress; show the reply.
    if result.tool_input is None:
        return OnboardingTurnResult(
            assistant_message=_sanitize(result.text),
            is_complete=False,
        )

    try:
        extraction = OnboardingExtraction.model_validate(result.tool_input)
    except Exception as exc:
        error = LLMError(f'LLM returned invalid onboarding extraction: {exc}')
        sink.on_error(error=error)
        raise error from None

    answers = _answers_from_extraction(extraction, persona_catalog)
    sink.on_parsed(parsed=extraction)

    # The assistant's closing line (sanitized) when present; otherwise a
    # neutral completion message. The member never sees the persona name.
    closing = _sanitize(result.text) or (
        "Thanks -- that's everything I need. We'll use this to prepare your "
        "plan."
    )
    return OnboardingTurnResult(
        assistant_message=closing,
        is_complete=True,
        extraction=extraction,
        answers=answers,
    )


def stream_onboarding_turn(
    transcript,
    *,
    member_message,
    persona_catalog,
    trace=None,
):
    """Stream one onboarding turn: yield text deltas, then the result.

    Streaming counterpart to :func:`run_onboarding_turn`. It is a
    generator that yields incremental ``str`` text deltas as the model
    produces the conversational reply, and finally yields a single
    :class:`OnboardingTurnResult` (the LAST item) that is IDENTICAL in
    shape to what :func:`run_onboarding_turn` produces for the same input.

    Single-generation contract (issue #821): one streaming turn makes
    exactly ONE model generation. The tool schema is attached to the
    ``llm.stream(...)`` call, so the SAME generation that produces the
    streamed conversational deltas also produces the structured tool call
    on the final turn. The authoritative :class:`OnboardingTurnResult` is
    assembled from that single generation's terminal ``done`` event — there
    is no second ``llm.complete`` round-trip, so the ``done`` event is
    emitted as soon as the last delta has been streamed. Concretely:

    - The opening turn (no member message, empty transcript) yields the
      deterministic greeting as a single delta then the greeting result,
      with no model call (mirrors :func:`run_onboarding_turn`).
    - Otherwise we open ``llm.stream(..., tools=[tool])`` and yield each
      text delta as it arrives. The streamed deltas reproduce the
      assistant's conversational reply token-by-token.
    - The terminal ``done`` event carries the fully assembled
      :class:`~integrations.services.llm.LLMResult` (text plus, on the
      final turn, ``tool_input``). We build the authoritative result from
      it via the SAME :func:`_turn_result_from_llm` helper the
      non-streaming path uses, so the persisted answers are byte-for-byte
      identical.

    This stays Django-independent (no models, no request, no ``django.db``)
    just like :func:`run_onboarding_turn`.

    Raises:
        LLMError: when opening the stream fails, the stream fails
            mid-response, or the final-turn tool input does not validate.
            A mid-stream failure (after at least one delta) surfaces as
            :class:`LLMError` from the generator so the transport can fall
            back to the non-streaming path for the same member message. No
            partial result is yielded, so the caller writes nothing.
    """
    sink = trace or TraceSink()

    # Opening turn: greet deterministically, no model call. Emit the
    # greeting as a single delta so the transport renders it uniformly.
    if member_message is None and not transcript:
        result = OnboardingTurnResult(
            assistant_message=GREETING, is_complete=False,
        )
        yield GREETING
        yield result
        return

    system = _build_system_prompt(persona_catalog)
    messages = _build_messages(transcript, member_message)
    tool = {
        'name': _TOOL_NAME,
        'description': (
            'Record the structured onboarding intake once the interview is '
            'complete.'
        ),
        'input_schema': OnboardingExtraction.model_json_schema(),
    }

    sink.on_request(system=system, messages=messages, tool=tool)

    # Stream the conversational text with the tool attached so this single
    # generation also yields the structured tool call on the final turn.
    # ``tool_choice`` is left to ``auto`` (the default) so the model only
    # emits the tool call when it judges the interview complete.
    llm_result = None
    started = time.monotonic()
    try:
        for event in llm.stream(messages, system=system, tools=[tool]):
            if event.is_done:
                llm_result = event.result
                break
            if event.text:
                yield event.text
    except LLMError as error:
        sink.on_error(error=error)
        raise
    latency_seconds = time.monotonic() - started

    if llm_result is None:
        # Defensive: a stream that ended without a terminal ``done`` event
        # gives us no authoritative result. Surface as LLMError so the
        # transport falls back rather than persisting an empty turn.
        error = LLMError('LLM stream ended without a terminal result')
        sink.on_error(error=error)
        raise error

    sink.on_result(result=llm_result, latency_seconds=latency_seconds)

    # Authoritative turn decision built from the SAME generation (no second
    # model round-trip), so the persisted answers are identical to the
    # non-streaming path.
    result = _turn_result_from_llm(llm_result, persona_catalog, sink)
    yield result


__all__ = [
    'SYSTEM_PROMPT',
    'GREETING',
    'OnboardingExtraction',
    'Message',
    'PersonaInfo',
    'PersonaQuestion',
    'ExtractedAnswer',
    'OnboardingTurnResult',
    'TraceSink',
    'PersonaSignal',
    'GoalCategory',
    'TimeProfile',
    'MainBlocker',
    'ProjectStage',
    'CareerDirection',
    'CodingAgentUse',
    'PlanHorizon',
    'run_onboarding_turn',
    'stream_onboarding_turn',
]
