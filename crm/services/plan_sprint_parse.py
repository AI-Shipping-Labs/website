"""Django-independent LLM parse of a `#plan-sprints` thread (issue #890, Phase 2).

A pure, Django-independent callable that reads one captured `#plan-sprints`
thread plus the member's current plan items and returns a structured progress
signal: which plan items the member reports as DONE, a short staff-facing
summary, and any blockers they raised.

Django-independence is a hard contract (mirrors
:mod:`integrations.services.feedback_synthesis`). This module imports neither
``django.db`` models, nor ``request`` objects, nor the ``plans`` / ``crm``
apps. The task helper that wraps it (``crm/tasks/ingest_plan_sprints.py``)
does all the ORM reads (assembling the plain :class:`PlanSprintParseInput`)
and all the ORM writes (flipping ``done_at`` + recording provenance). Keeping
the AI logic here, a sibling of the LLM service, means it can be driven
against a real provider without dragging in the request/ORM layer. An
import-isolation test enforces this seam.

The only dependency is the provider-neutral LLM service from #799
(:mod:`integrations.services.llm`).

Structured output uses the #799 pattern: the :class:`PlanSprintParseResult`
Pydantic model doubles as the tool input schema via ``model_json_schema()``;
the model returns its answer as a single forced tool call whose validated
input is the result.

Out of scope (do NOT build here): applying the result, the watermark /
idempotency logic, dropping hallucinated ids — those belong to the ORM-aware
caller. This callable only parses; it never mutates anything.
"""

import time

from pydantic import BaseModel, Field

from integrations.services import llm
from integrations.services.llm import LLMError

# Versioned via this module-level constant so future eval/diffing can track
# prompt revisions across stored runs. Keep it concise.
SYSTEM_PROMPT = (
    'You read a Slack thread from a #plan-sprints channel of a hands-on AI '
    'engineering cohort program, where a member posts updates on their '
    'current sprint plan. You are also given the member\'s CURRENT plan '
    'items, each with a stable id. Decide which of those plan items the '
    'thread indicates the member has now COMPLETED (done), write a short '
    'staff-facing summary of what they reported this period, and list any '
    'explicit blockers or risks they raised.\n\n'
    'Rules:\n'
    '- Only mark an item complete when the thread clearly indicates it is '
    'finished or done. Be conservative: when in doubt, do not mark it.\n'
    '- Reference ONLY the item ids you were given. Never invent an id and '
    'never mark an item the member did not actually report finishing.\n'
    '- An item already shown as done needs no action, but you may still '
    'include it if the member reaffirms it; the caller deduplicates.\n'
    '- The summary is for staff context only; be specific and grounded in '
    'what the member wrote. Do not invent progress that was not reported.\n'
    '- blockers is empty when the member raised none.'
)

# Name of the structured-output tool the model is forced to call.
_TOOL_NAME = 'plan_sprint_progress'

# Valid plan-item kinds. Kept as plain constants (no Django import).
ITEM_KIND_CHECKPOINT = 'checkpoint'
ITEM_KIND_DELIVERABLE = 'deliverable'
ITEM_KIND_NEXT_STEP = 'next_step'
ITEM_KINDS = (ITEM_KIND_CHECKPOINT, ITEM_KIND_DELIVERABLE, ITEM_KIND_NEXT_STEP)


class ParsedCompletion(BaseModel):
    """A single plan item the thread indicates is now done."""

    item_kind: str = Field(
        description=(
            'The kind of plan item: one of "checkpoint", "deliverable", '
            'or "next_step".'
        ),
    )
    item_id: int = Field(
        description=(
            'The stable id of the plan item, exactly as given in the '
            'supplied plan items list. Never invent an id.'
        ),
    )
    confidence: float = Field(
        default=1.0,
        description=(
            'How confident you are this item is complete, from 0.0 to 1.0.'
        ),
        ge=0.0,
        le=1.0,
    )


class PlanSprintParseResult(BaseModel):
    """Structured progress signal parsed from one `#plan-sprints` thread.

    Doubles as the LLM structured-output schema (via ``model_json_schema()``)
    and the callable's return type. Validating ``result.tool_input`` against
    this model is what guarantees the callable returns this exact shape.
    """

    completed_items: list[ParsedCompletion] = Field(
        default_factory=list,
        description=(
            'The plan items the thread indicates the member has now '
            'completed. Empty when the thread reports no completions.'
        ),
    )
    summary: str = Field(
        default='',
        description=(
            'A short staff-facing summary of what the member reported this '
            'period. Empty when the thread carries no substantive update.'
        ),
    )
    blockers: list[str] = Field(
        default_factory=list,
        description=(
            'Explicit blockers or risks the member raised. Empty when none.'
        ),
    )


class PlanSprintParseUnavailable(LLMError):
    """Raised when a parse is requested but the LLM service is disabled.

    Subclasses :class:`LLMError` so callers that catch the generic LLM
    failure also catch this, while the ingest caller can branch on the type
    to treat "not configured" as "skip parsing, no mutation" rather than a
    run-failing error.
    """


class _PlanItem(BaseModel):
    """One plan item offered to the model, with a stable id."""

    item_kind: str
    item_id: int
    description: str
    already_done: bool = False


class PlanSprintParseInput(BaseModel):
    """Plain (ORM-free) input the caller assembles for a parse.

    The task helper reads the captured thread + the member's current plan
    and maps them onto this model; the callable never touches the database.
    """

    member_name: str = ''
    plan_goal: str = ''
    # Full thread, root first: (author_display, posted_at_iso, text).
    messages: list[tuple[str, str, str]] = Field(default_factory=list)
    plan_items: list[_PlanItem] = Field(default_factory=list)


class TraceSink:
    """No-op trace sink; the default when ``trace`` is omitted.

    Mirrors the feedback-synthesis sink so a future eval runner can capture
    each run's prompt, messages, tool spec, raw result, latency, and parsed
    output against a real provider. Every hook defaults to a no-op so
    production runs stay silent.
    """

    def on_request(self, *, system, messages, tool):
        """Called just before ``llm.complete`` with the rendered request."""

    def on_result(self, *, result, latency_seconds):
        """Called after ``llm.complete`` returns, with the raw result."""

    def on_parsed(self, *, parsed):
        """Called after the tool input validates into a result model."""

    def on_error(self, *, error):
        """Called when parsing/validation or the LLM call fails."""


def _build_user_message(data):
    """Render the user message text from the plain input."""
    lines = []
    if data.member_name:
        lines.append(f'Member: {data.member_name}')
    if data.plan_goal:
        lines.append(f'Plan goal: {data.plan_goal}')
    lines.append('')
    lines.append('Current plan items (mark only these, by id):')
    if data.plan_items:
        for item in data.plan_items:
            done_flag = ' [already done]' if item.already_done else ''
            lines.append(
                f'- {item.item_kind} id={item.item_id}{done_flag}: '
                f'{item.description}'
            )
    else:
        lines.append('(none)')
    lines.append('')
    lines.append('Slack thread (root first):')
    for author, posted_at, text in data.messages:
        body = (text or '').strip() or '(no text)'
        lines.append('')
        lines.append(f'[{posted_at}] {author}:')
        lines.append(body)
    return '\n'.join(lines)


def parse_plan_sprint_thread(data, *, trace=None):
    """Parse one `#plan-sprints` thread into a structured progress signal.

    Args:
        data: A :class:`PlanSprintParseInput` (assembled by the caller from
            ORM reads -- this function reads no database).
        trace: Optional :class:`TraceSink` (or compatible) recording the
            prompt, messages, tool spec, raw result, latency, and parsed
            output for the run. ``None`` runs silently.

    Returns:
        PlanSprintParseResult: the validated progress signal.

    Raises:
        PlanSprintParseUnavailable: when ``llm.is_enabled()`` is False.
            ``llm.complete`` is never called in this case.
        LLMError: when the LLM call fails or its output cannot be validated.
            No partial result is returned.
    """
    sink = trace or TraceSink()

    # Gate: never call the model when the service is disabled.
    if not llm.is_enabled():
        raise PlanSprintParseUnavailable(
            'AI parsing is not configured (no LLM provider).'
        )

    tool = {
        'name': _TOOL_NAME,
        'description': (
            'Return the structured progress parsed from the thread.'
        ),
        'input_schema': PlanSprintParseResult.model_json_schema(),
    }
    messages = [
        {'role': 'user', 'content': _build_user_message(data)},
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
        error = LLMError('LLM did not return structured progress output.')
        sink.on_error(error=error)
        raise error

    try:
        parsed = PlanSprintParseResult.model_validate(result.tool_input)
    except Exception as exc:
        error = LLMError(f'LLM returned invalid progress output: {exc}')
        sink.on_error(error=error)
        raise error from None

    # Defensively drop any completion that names an unknown kind. The caller
    # additionally drops ids that are not on the member's plan (hallucinated
    # ids), since this Django-independent module does not know the plan.
    parsed.completed_items = [
        item for item in parsed.completed_items if item.item_kind in ITEM_KINDS
    ]
    sink.on_parsed(parsed=parsed)
    return parsed
