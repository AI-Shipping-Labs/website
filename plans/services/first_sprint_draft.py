"""First-sprint plan draft callable (issue #1205).

Pure, Django-independent LLM wrapper. The caller assembles plain input from
onboarding/member context and persists the validated result separately.
"""

import time

from pydantic import BaseModel, Field

from integrations.services import llm
from integrations.services.llm import LLMError

SYSTEM_PROMPT = (
    'You are a sprint-planning assistant for AI Shipping Labs staff. A member '
    'requested their first sprint plan and submitted onboarding. Draft a plan '
    'staff can review and edit before sharing. Use onboarding answers as the '
    'source of truth for member-facing plan text. CRM/persona/recent activity '
    'context may inform staff-only internal notes and rationale, but do not '
    'copy internal CRM/staff-note text into member-facing fields. Do not invent '
    'facts. Return exactly the requested structured output.'
)

_TOOL_NAME = 'first_sprint_plan_draft'


class DraftResource(BaseModel):
    title: str = ''
    url: str = ''
    note: str = ''


class DraftWeek(BaseModel):
    week_number: int
    theme: str = ''
    checkpoints: list[str] = Field(default_factory=list)


class FirstSprintDraftResult(BaseModel):
    title: str = ''
    goal: str = ''
    summary_current_situation: str = ''
    summary_goal: str = ''
    summary_main_gap: str = ''
    summary_weekly_hours: str = ''
    summary_why_this_plan: str = ''
    focus_main: str = ''
    focus_supporting: list[str] = Field(default_factory=list)
    accountability: str = ''
    weeks: list[DraftWeek] = Field(default_factory=list)
    resources: list[DraftResource] = Field(default_factory=list)
    deliverables: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(
        default_factory=list,
        description='Pre-sprint actions to create before the sprint begins.',
    )
    internal_notes: str = Field(
        default='',
        description='Staff-only interpretation, caveats, and review notes.',
    )
    rationale: str = Field(
        default='',
        description='Staff-only explanation of the source signals used.',
    )


class FirstSprintDraftUnavailable(LLMError):
    """Raised when first-plan drafting is requested but LLM is disabled."""


class OnboardingAnswer(BaseModel):
    prompt: str = ''
    answer: str = ''


class RecentActivity(BaseModel):
    occurred_at: str = ''
    category: str = ''
    type_label: str = ''
    label: str = ''


class FirstSprintDraftInput(BaseModel):
    member_label: str = ''
    sprint_name: str = ''
    sprint_duration_weeks: int
    persona: str = ''
    onboarding_answers: list[OnboardingAnswer] = Field(default_factory=list)
    recent_activity: list[RecentActivity] = Field(default_factory=list)
    crm_summary: str = ''
    crm_next_steps: str = ''


class TraceSink:
    def on_request(self, *, system, messages, tool):
        """Called before the LLM request."""

    def on_result(self, *, result, latency_seconds):
        """Called after the LLM request."""

    def on_parsed(self, *, parsed):
        """Called after structured output validation."""

    def on_error(self, *, error):
        """Called when the call or parse fails."""


def _render_answers(answers):
    rows = [a for a in answers if a.prompt.strip() and a.answer.strip()]
    if not rows:
        return ['Onboarding answers:', '  (none)']
    lines = ['Onboarding answers:']
    for answer in rows:
        lines.append(f'  - {answer.prompt.strip()}: {answer.answer.strip()}')
    return lines


def _render_activity(rows):
    activity = [
        row for row in rows
        if row.occurred_at.strip() and row.type_label.strip()
    ]
    if not activity:
        return []
    lines = ['Recent activity:']
    for row in activity:
        category = f'[{row.category}] ' if row.category else ''
        lines.append(
            f'  - {row.occurred_at} {category}{row.type_label}: {row.label}'
        )
    return lines


def _build_user_message(draft_input):
    lines = [
        f'Member: {draft_input.member_label}',
        f'Sprint: {draft_input.sprint_name}',
        f'Sprint duration: {draft_input.sprint_duration_weeks} weeks',
        '',
        '=== Member-submitted onboarding ===',
    ]
    lines.extend(_render_answers(draft_input.onboarding_answers))

    context_lines = []
    if draft_input.persona.strip():
        context_lines.append(f'Persona: {draft_input.persona.strip()}')
    if draft_input.crm_summary.strip():
        context_lines.append(f'CRM summary: {draft_input.crm_summary.strip()}')
    if draft_input.crm_next_steps.strip():
        context_lines.append(
            f'CRM next steps: {draft_input.crm_next_steps.strip()}'
        )
    context_lines.extend(_render_activity(draft_input.recent_activity))
    if context_lines:
        lines.extend([
            '',
            '=== Staff-only context for interpretation ===',
            'Use this only for internal_notes/rationale or broad judgment; do '
            'not copy it verbatim into member-facing plan fields.',
        ])
        lines.extend(context_lines)

    return '\n'.join(lines)


def _validate_week_count(parsed, expected_weeks):
    if len(parsed.weeks) != expected_weeks:
        raise LLMError(
            'LLM returned invalid first-plan draft output: '
            f'expected {expected_weeks} weeks, got {len(parsed.weeks)}.'
        )
    expected_numbers = list(range(1, expected_weeks + 1))
    actual_numbers = [week.week_number for week in parsed.weeks]
    if actual_numbers != expected_numbers:
        raise LLMError(
            'LLM returned invalid first-plan draft output: '
            f'week_number must be {expected_numbers}.'
        )


def draft_first_sprint(draft_input, *, trace=None):
    """Draft first-sprint plan content from submitted onboarding."""
    sink = trace or TraceSink()
    if not llm.is_enabled():
        raise FirstSprintDraftUnavailable(
            'AI draft is not configured (no LLM provider).'
        )

    tool = {
        'name': _TOOL_NAME,
        'description': 'Return the structured first-sprint plan draft.',
        'input_schema': FirstSprintDraftResult.model_json_schema(),
    }
    messages = [{'role': 'user', 'content': _build_user_message(draft_input)}]
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
    sink.on_result(result=result, latency_seconds=time.monotonic() - started)

    if result.tool_input is None:
        error = LLMError('LLM did not return structured first-plan output.')
        sink.on_error(error=error)
        raise error

    try:
        parsed = FirstSprintDraftResult.model_validate(result.tool_input)
        _validate_week_count(parsed, draft_input.sprint_duration_weeks)
    except LLMError as error:
        sink.on_error(error=error)
        raise
    except Exception as exc:
        error = LLMError(f'LLM returned invalid first-plan draft output: {exc}')
        sink.on_error(error=error)
        raise error from None

    sink.on_parsed(parsed=parsed)
    return parsed
