"""Sprint-feedback synthesis callable (issue #805).

A pure, Django-independent function that turns the free-text + numeric
answers collected from a sprint's enrolled members into a structured
retrospective: recurring themes, what went well, what to improve,
concrete next-sprint recommendations, and the stated intent to return.

Django-independence is a hard contract. This module imports neither
``django.db`` models, nor ``request`` objects, nor the ``plans`` /
``questionnaires`` apps. The Studio view (and the future #809 eval
runner) is a thin wrapper: it does the ORM reads, maps them to the
plain :class:`SprintFeedbackInput` dataclass, calls
:func:`synthesize_feedback`, and persists the result. Keeping the AI
logic here (a sibling of the LLM service) means #809 can drive it
against a real provider without dragging in the request layer. An
import-isolation test enforces this so the seam stays clean.

The only dependency is the provider-neutral LLM service from #799
(:mod:`integrations.services.llm`), which is itself vendor-neutral.

Structured output uses the #799 pattern: the
:class:`FeedbackSynthesisResult` Pydantic model doubles as the
tool input schema via ``model_json_schema()``; the model returns its
answer as a single tool call whose validated input is the result.

Note (out of scope, do NOT build here): feedback could later be
collected conversationally via a chat interviewer rather than from the
static form -- that is the #804-style direction applied to feedback and
is explicitly not built in this issue. This callable only synthesizes
already-collected answers.
"""

import time

from pydantic import BaseModel, Field

from integrations.services import llm
from integrations.services.llm import LLMError

# The system prompt is versioned via this module-level constant so #809
# can diff prompt revisions across stored runs. Keep it concise.
SYSTEM_PROMPT = (
    'You are a sprint-retrospective analyst for a hands-on AI engineering '
    'cohort program. You are given the free-text and numeric feedback that '
    'members submitted at the end of a sprint. Read all of it and produce a '
    'concise, planning-ready synthesis for the next sprint.\n\n'
    'Synthesize across responses -- do not just echo individual quotes. '
    'Identify recurring themes ordered by how many members raised them, '
    'summarise what went well and what should improve, and propose concrete, '
    'actionable recommendations (each with a short rationale) the team can '
    'apply when planning the next sprint. When members indicated whether they '
    'intend to join the next sprint, summarise that signal. Be specific and '
    'grounded in the feedback; do not invent details that were not provided.'
)

# Name of the structured-output tool the model is forced to call.
_TOOL_NAME = 'sprint_feedback_synthesis'


class FeedbackTheme(BaseModel):
    """A recurring topic raised across responses."""

    title: str = Field(description='Short label for the theme.')
    summary: str = Field(
        description='One- to three-sentence summary of the theme.'
    )
    supporting_count: int = Field(
        description='How many responses raised this theme.',
        ge=0,
    )


class FeedbackRecommendation(BaseModel):
    """A concrete action for next-sprint planning, with rationale."""

    recommendation: str = Field(
        description='A concrete, actionable recommendation.'
    )
    rationale: str = Field(
        description='Why this recommendation follows from the feedback.'
    )


class FeedbackSynthesisResult(BaseModel):
    """Structured synthesis of a sprint's collected feedback.

    Doubles as the LLM structured-output schema (via
    ``model_json_schema()``) and the callable's return type. Validating
    ``result.tool_input`` against this model is what guarantees the
    callable returns this exact shape.
    """

    themes: list[FeedbackTheme] = Field(
        default_factory=list,
        description=(
            'Recurring topics across responses, ordered by prevalence '
            '(most-raised first).'
        ),
    )
    what_went_well: list[str] = Field(
        default_factory=list,
        description='Concise points members were positive about.',
    )
    what_to_improve: list[str] = Field(
        default_factory=list,
        description='Concise points members want changed.',
    )
    recommendations: list[FeedbackRecommendation] = Field(
        default_factory=list,
        description='Concrete actions for next-sprint planning/strategy.',
    )
    next_sprint_signal: str = Field(
        default='',
        description=(
            'Short summary of stated intent to join the next sprint, drawn '
            'from the relevant choice/text answers. Empty when no such '
            'signal was present.'
        ),
    )
    response_count: int = Field(
        default=0,
        description='Number of responses synthesized.',
        ge=0,
    )


class FeedbackSynthesisUnavailable(LLMError):
    """Raised when synthesis is requested but the LLM service is disabled.

    Subclasses :class:`LLMError` so callers that catch the generic LLM
    failure also catch this, while callers that want to distinguish the
    "not configured" case from a transport failure can branch on the
    type.
    """


class FeedbackSynthesisEmpty(Exception):
    """Raised when there is no submitted feedback to synthesize.

    Distinct from :class:`LLMError`: nothing went wrong with the model;
    there was simply nothing to summarize, and no LLM call is made.
    """


class _SingleResponse(BaseModel):
    """One member's submitted feedback as ``(question, type, answer)`` rows.

    The free-text answers are the primary signal; numeric/choice answers
    are carried as labeled context. ``answers`` is a list of
    ``(question_text, question_type, answer_text)`` tuples.
    """

    answers: list[tuple[str, str, str]] = Field(default_factory=list)


class SprintFeedbackInput(BaseModel):
    """Plain (ORM-free) input the caller assembles for synthesis.

    The Studio view reads the submitted responses and maps them onto
    this model; the callable never touches the database.
    """

    sprint_name: str
    start_date: str = ''
    duration_weeks: int | None = None
    response_count: int = 0
    responses: list[_SingleResponse] = Field(default_factory=list)


class TraceSink:
    """No-op trace sink; the default when ``trace`` is omitted.

    The #809 eval runner subclasses this (or passes any object with the
    same methods) to capture each run's prompt, messages, tool spec, raw
    result, latency, and parsed output against a real provider. Every
    hook defaults to doing nothing so production runs stay silent.
    """

    def on_request(self, *, system, messages, tool):
        """Called just before ``llm.complete`` with the rendered request."""

    def on_result(self, *, result, latency_seconds):
        """Called after ``llm.complete`` returns, with the raw result."""

    def on_parsed(self, *, parsed):
        """Called after the tool input validates into a result model."""

    def on_error(self, *, error):
        """Called when parsing/validation or the LLM call fails."""


def _build_user_message(feedback):
    """Render the user message text from the plain input."""
    lines = [f'Sprint: {feedback.sprint_name}']
    if feedback.start_date:
        lines.append(f'Start date: {feedback.start_date}')
    if feedback.duration_weeks:
        lines.append(f'Duration: {feedback.duration_weeks} weeks')
    lines.append(f'Number of submitted responses: {feedback.response_count}')
    lines.append('')
    lines.append('Submitted feedback follows. Each response is from one member.')
    for index, response in enumerate(feedback.responses, start=1):
        lines.append('')
        lines.append(f'--- Response {index} ---')
        for question_text, question_type, answer_text in response.answers:
            answer = (answer_text or '').strip() or '(no answer)'
            lines.append(f'Q ({question_type}): {question_text}')
            lines.append(f'A: {answer}')
    return '\n'.join(lines)


def synthesize_feedback(feedback, *, trace=None):
    """Synthesize collected sprint feedback into a structured retrospective.

    Args:
        feedback: A :class:`SprintFeedbackInput` (assembled by the caller
            from ORM reads -- this function reads no database).
        trace: Optional :class:`TraceSink` (or compatible) recording the
            prompt, messages, tool spec, raw result, latency, and parsed
            output for the run. ``None`` runs silently.

    Returns:
        FeedbackSynthesisResult: the validated synthesis.

    Raises:
        FeedbackSynthesisUnavailable: when ``llm.is_enabled()`` is False.
            ``llm.complete`` is never called in this case.
        FeedbackSynthesisEmpty: when there are zero responses to
            synthesize. ``llm.complete`` is never called in this case.
        LLMError: when the LLM call fails or its output cannot be
            validated. No partial result is returned.
    """
    sink = trace or TraceSink()

    # Gate: never call the model when the service is disabled or there is
    # nothing to summarize.
    if not llm.is_enabled():
        raise FeedbackSynthesisUnavailable(
            'AI synthesis is not configured (no LLM provider).'
        )
    if not feedback.responses:
        raise FeedbackSynthesisEmpty(
            'No submitted feedback to summarize.'
        )

    tool = {
        'name': _TOOL_NAME,
        'description': (
            'Return the structured synthesis of the sprint feedback.'
        ),
        'input_schema': FeedbackSynthesisResult.model_json_schema(),
    }
    messages = [
        {'role': 'user', 'content': _build_user_message(feedback)},
    ]

    sink.on_request(system=SYSTEM_PROMPT, messages=messages, tool=tool)

    started = time.monotonic()
    try:
        result = llm.complete(
            messages,
            system=SYSTEM_PROMPT,
            tools=[tool],
            tool_choice={'type': 'tool', 'name': _TOOL_NAME},
        )
    except LLMError as error:
        sink.on_error(error=error)
        raise
    latency_seconds = time.monotonic() - started
    sink.on_result(result=result, latency_seconds=latency_seconds)

    if result.tool_input is None:
        error = LLMError(
            'LLM did not return structured feedback synthesis output.'
        )
        sink.on_error(error=error)
        raise error

    try:
        parsed = FeedbackSynthesisResult.model_validate(result.tool_input)
    except Exception as exc:
        error = LLMError(
            f'LLM returned invalid synthesis output: {exc}'
        )
        sink.on_error(error=error)
        raise error from None

    # Echo the count we actually synthesized so a stale model value never
    # disagrees with the input the caller provided.
    parsed.response_count = feedback.response_count
    sink.on_parsed(parsed=parsed)
    return parsed
