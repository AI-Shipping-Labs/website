"""Next-sprint plan draft callable (issue #891, Phase 3).

A pure, Django-independent function that drafts the narrative text for a
member's NEXT-sprint plan from their recent ``#plan-sprints`` updates and
their current plan state. It produces FORWARD-LOOKING suggestions for
staff to review — it never writes anything into a plan. The Studio view
(and the plans API) is a thin wrapper: it does the ORM reads, maps them
onto the plain :class:`NextSprintDraftInput` dataclass, calls
:func:`draft_next_sprint`, and persists the validated result aside in a
``NextSprintPlanDraft`` row.

Django-independence is a hard contract, mirroring
:mod:`integrations.services.feedback_synthesis` and
:mod:`crm.services.plan_sprint_parse`. This module imports neither
``django.db`` models, nor ``request`` objects, nor the ``plans`` / ``crm``
apps. An import-isolation test enforces the seam.

The only dependency is the provider-neutral LLM service from #799
(:mod:`integrations.services.llm`).

Structured output uses the #799 pattern: the
:class:`NextSprintDraftResult` Pydantic model doubles as the tool input
schema via ``model_json_schema()``; the model returns its answer as a
single tool call whose validated input is the result.
"""

import time

from pydantic import BaseModel, Field

from integrations.services import llm
from integrations.services.llm import LLMError

# Versioned via this module-level constant so future eval harnesses can
# diff prompt revisions across stored runs.
SYSTEM_PROMPT = (
    'You are a sprint-planning assistant for a hands-on AI engineering '
    'cohort program. A staff member is preparing a participant\'s plan for '
    'the NEXT sprint. You are given (a) the participant\'s current plan '
    'state — their goal, narrative summary, and their done vs. not-done '
    'checkpoints, deliverables, and next steps — and (b) the text of their '
    'most recent #plan-sprints Slack updates, newest first.\n\n'
    'Draft forward-looking plan content for the NEXT sprint, grounded in '
    'what the participant actually reported and where they currently are. '
    'Carry the momentum of finished work forward, address the unfinished '
    'items and stated blockers, and keep the goal realistic for the stated '
    'weekly hours. Be specific and concrete; do not invent facts that are '
    'not supported by the updates or current state. When there are no '
    'recent updates, lean on the current plan state alone. Everything you '
    'produce is a SUGGESTION a staff member will review and edit — write it '
    'as ready-to-copy plan text, not as a message to the participant.'
)

# Name of the structured-output tool the model is forced to call.
_TOOL_NAME = 'next_sprint_plan_draft'


class NextSprintDraftResult(BaseModel):
    """Structured draft of a member's next-sprint plan narrative.

    Doubles as the LLM structured-output schema (via
    ``model_json_schema()``) and the callable's return type. Validating
    ``result.tool_input`` against this model is what guarantees the
    callable returns this exact shape. The four ``summary_*`` fields and
    ``goal`` mirror the plan summary block so staff can copy them straight
    across; ``suggested_next_steps`` are rendered as editable text and do
    NOT create ``NextStep`` rows.
    """

    summary_current_situation: str = Field(
        default='',
        description=(
            'Proposed "current situation" narrative for the next sprint, '
            'reflecting where the member is now after the recent updates.'
        ),
    )
    summary_goal: str = Field(
        default='',
        description='Proposed "goal" narrative for the next sprint.',
    )
    summary_main_gap: str = Field(
        default='',
        description=(
            'Proposed "main gap" narrative — what is still missing or '
            'blocking, grounded in the updates and unfinished items.'
        ),
    )
    summary_weekly_hours: str = Field(
        default='',
        description=(
            'Proposed weekly-hours note for the next sprint (short text, '
            'e.g. "~6 hours/week").'
        ),
    )
    goal: str = Field(
        default='',
        description='A concise one-line plan goal for the next sprint.',
    )
    suggested_next_steps: list[str] = Field(
        default_factory=list,
        description=(
            'Concrete suggested next steps for the next sprint, as editable '
            'text. These are SUGGESTIONS — they are not created as plan '
            'items by the callable.'
        ),
    )
    rationale: str = Field(
        default='',
        description=(
            'A short staff-facing note on what in the updates and current '
            'progress drove this draft.'
        ),
    )


class NextSprintDraftUnavailable(LLMError):
    """Raised when a draft is requested but the LLM service is disabled.

    Subclasses :class:`LLMError` so callers that catch the generic LLM
    failure also catch this, while callers that want to distinguish the
    "not configured" case from a transport failure can branch on the type.
    """


class RecentUpdate(BaseModel):
    """One captured ``#plan-sprints`` message: ``(author, posted_at, text)``."""

    author_display: str = ''
    posted_at: str = ''
    text: str = ''


class NextSprintDraftInput(BaseModel):
    """Plain (ORM-free) input the caller assembles for the draft.

    The Studio view / API reads the destination plan, the carry-over
    source plan's recent threads, and the destination sprint metadata, and
    maps them onto this model; the callable never touches the database.
    An empty ``recent_updates`` list is allowed — the draft then leans on
    plan state only.
    """

    member_label: str = ''
    current_sprint_name: str = ''
    next_sprint_name: str = ''
    next_sprint_duration_weeks: int | None = None

    # "Where the member is now" — current plan state.
    goal: str = ''
    summary_current_situation: str = ''
    summary_goal: str = ''
    summary_main_gap: str = ''
    summary_weekly_hours: str = ''

    done_checkpoints: list[str] = Field(default_factory=list)
    not_done_checkpoints: list[str] = Field(default_factory=list)
    done_deliverables: list[str] = Field(default_factory=list)
    not_done_deliverables: list[str] = Field(default_factory=list)
    done_next_steps: list[str] = Field(default_factory=list)
    not_done_next_steps: list[str] = Field(default_factory=list)

    recent_updates: list[RecentUpdate] = Field(default_factory=list)


class TraceSink:
    """No-op trace sink; the default when ``trace`` is omitted.

    An eval harness subclasses this (or passes any object with the same
    methods) to capture each run's prompt, messages, tool spec, raw
    result, latency, and parsed output against a real provider. Every hook
    defaults to doing nothing so production runs stay silent.
    """

    def on_request(self, *, system, messages, tool):
        """Called just before ``llm.complete`` with the rendered request."""

    def on_result(self, *, result, latency_seconds):
        """Called after ``llm.complete`` returns, with the raw result."""

    def on_parsed(self, *, parsed):
        """Called after the tool input validates into a result model."""

    def on_error(self, *, error):
        """Called when parsing/validation or the LLM call fails."""


def _render_items(label, items):
    """Render a labelled bullet block, or a "(none)" line when empty."""
    lines = [f'{label}:']
    if not items:
        lines.append('  (none)')
        return lines
    for item in items:
        text = (item or '').strip()
        if text:
            lines.append(f'  - {text}')
    if len(lines) == 1:
        lines.append('  (none)')
    return lines


def _build_user_message(draft_input):
    """Render the user message text from the plain input."""
    lines = []
    if draft_input.member_label:
        lines.append(f'Member: {draft_input.member_label}')
    if draft_input.current_sprint_name:
        lines.append(f'Current sprint: {draft_input.current_sprint_name}')
    if draft_input.next_sprint_name:
        lines.append(f'Next sprint: {draft_input.next_sprint_name}')
    if draft_input.next_sprint_duration_weeks:
        lines.append(
            f'Next sprint duration: '
            f'{draft_input.next_sprint_duration_weeks} weeks'
        )

    lines.append('')
    lines.append('=== Current plan state ===')
    lines.append(f'Goal: {draft_input.goal or "(none)"}')
    lines.append(
        f'Current situation: {draft_input.summary_current_situation or "(none)"}'
    )
    lines.append(f'Summary goal: {draft_input.summary_goal or "(none)"}')
    lines.append(f'Main gap: {draft_input.summary_main_gap or "(none)"}')
    lines.append(f'Weekly hours: {draft_input.summary_weekly_hours or "(none)"}')
    lines.append('')
    lines.extend(_render_items('Done checkpoints', draft_input.done_checkpoints))
    lines.extend(
        _render_items('Unfinished checkpoints', draft_input.not_done_checkpoints)
    )
    lines.extend(_render_items('Done deliverables', draft_input.done_deliverables))
    lines.extend(
        _render_items('Unfinished deliverables', draft_input.not_done_deliverables)
    )
    lines.extend(_render_items('Done next steps', draft_input.done_next_steps))
    lines.extend(
        _render_items('Unfinished next steps', draft_input.not_done_next_steps)
    )

    lines.append('')
    lines.append('=== Recent #plan-sprints updates (newest first) ===')
    if not draft_input.recent_updates:
        lines.append('(no recent updates — draft from plan state only)')
    else:
        for index, update in enumerate(draft_input.recent_updates, start=1):
            author = (update.author_display or '').strip() or 'unknown'
            when = (update.posted_at or '').strip()
            header = f'--- Update {index} ({author}'
            header += f', {when})' if when else ')'
            lines.append('')
            lines.append(header)
            lines.append((update.text or '').strip() or '(no text)')

    return '\n'.join(lines)


def draft_next_sprint(draft_input, *, trace=None):
    """Draft next-sprint plan narrative text from updates + current state.

    Args:
        draft_input: A :class:`NextSprintDraftInput` (assembled by the
            caller from ORM reads — this function reads no database).
        trace: Optional :class:`TraceSink` (or compatible) recording the
            prompt, messages, tool spec, raw result, latency, and parsed
            output for the run. ``None`` runs silently.

    Returns:
        NextSprintDraftResult: the validated draft.

    Raises:
        NextSprintDraftUnavailable: when ``llm.is_enabled()`` is False.
            ``llm.complete`` is never called in this case.
        LLMError: when the LLM call fails or its output cannot be
            validated. No partial result is returned.
    """
    sink = trace or TraceSink()

    # Gate: never call the model when the service is disabled.
    if not llm.is_enabled():
        raise NextSprintDraftUnavailable(
            'AI draft is not configured (no LLM provider).'
        )

    tool = {
        'name': _TOOL_NAME,
        'description': (
            'Return the structured draft of the next-sprint plan narrative.'
        ),
        'input_schema': NextSprintDraftResult.model_json_schema(),
    }
    messages = [
        {'role': 'user', 'content': _build_user_message(draft_input)},
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
        error = LLMError('LLM did not return structured draft output.')
        sink.on_error(error=error)
        raise error

    try:
        parsed = NextSprintDraftResult.model_validate(result.tool_input)
    except Exception as exc:
        error = LLMError(f'LLM returned invalid draft output: {exc}')
        sink.on_error(error=error)
        raise error from None

    sink.on_parsed(parsed=parsed)
    return parsed
