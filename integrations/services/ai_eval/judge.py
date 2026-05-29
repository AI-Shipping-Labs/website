"""LLM judge for the AI-eval suite (issue #812).

A per-assistant judge that reads a scenario's input plus the assistant's
structured output and decides, against a plain correctness rubric, whether
the output is a ``pass`` or a ``fail``. The judge follows the buildcamp v2
``06-evaluation`` shape: structured output with the reasoning written
BEFORE the label, one correctness dimension per assistant (no multi-
dimension "god evaluator" -- that is a documented future option).

The judge reuses the #799 structured-output path exactly like the two
callables do: a Pydantic model (:class:`JudgeVerdict`) doubles as the tool
input schema via ``model_json_schema()``, and the model is forced to call
that tool. On a live eval run this hits the real provider; in CI the #809
``patch_llm`` stub returns a schema-valid canned verdict, so the whole
flow runs mocked with no key or network.

This module is Django-independent (it imports only the LLM service and
Pydantic) and never imports or initializes Logfire (#813).
"""

import json
import time
from enum import Enum

from pydantic import BaseModel, Field

from integrations.services import llm
from integrations.services.llm import LLMError

# Judge prompt version, recorded in the eval report so experiments that
# change the judge prompt are comparable. Bump on any prompt edit.
JUDGE_PROMPT_VERSION = 'v1'

_TOOL_NAME = 'record_verdict'


class JudgeLabel(str, Enum):
    # ``pass`` is listed first so the #809 mock stub (which picks the first
    # enum value) returns a ``pass`` verdict by default; alignment tests
    # override the verdict explicitly where a ``fail`` is needed.
    PASS = 'pass'
    FAIL = 'fail'


class JudgeVerdict(BaseModel):
    """Structured judge output: reasoning first, then the binary label.

    The field order is deliberate -- ``reasoning`` precedes ``label`` so
    the model reasons before committing to a verdict (chain-of-thought in
    the structured output, per the methodology). ``failure_category`` is an
    optional short emergent tag, only meaningful on a ``fail``.
    """

    reasoning: str = Field(
        description=(
            'Step-by-step reasoning about whether the output satisfies the '
            'rubric. Write this BEFORE deciding the label.'
        ),
    )
    label: JudgeLabel = Field(
        description='Final verdict: "pass" if correct, "fail" otherwise.',
    )
    failure_category: str = Field(
        default='',
        description=(
            'Short tag for the kind of failure (e.g. persona-leak, '
            'hallucination, missing-field, wrong-scope). Empty on a pass.'
        ),
    )


# --- Rubrics (the single correctness dimension per assistant) ---

ONBOARDING_RUBRIC = (
    'You are grading one turn of an AI onboarding interviewer for a hands-on '
    'AI engineering cohort. Decide PASS or FAIL on the single dimension of '
    'CORRECTNESS.\n\n'
    'PASS when ALL hold:\n'
    '- The assistant message is on-topic for onboarding (goals, time, '
    'blockers, project scope, accountability) and does not get derailed by '
    'unrelated or adversarial member input.\n'
    '- No internal persona codename (Alex, Priya, Sam, Taylor) appears in '
    'anything shown to the member.\n'
    '- If the interview completed (an extraction is present), the extraction '
    'is internally consistent, uses valid enum values, and populates the '
    'required fields; it does not invent facts the member never stated.\n\n'
    'FAIL when any of those is violated (persona-name leak, off-scope or '
    'hijacked reply, invalid/missing extraction fields, or hallucinated '
    'facts).'
)

FEEDBACK_RUBRIC = (
    'You are grading the output of a sprint-feedback synthesizer for a '
    'hands-on AI engineering cohort. Decide PASS or FAIL on the single '
    'dimension of CORRECTNESS.\n\n'
    'PASS when ALL hold:\n'
    '- Every theme is supported by the submitted feedback; nothing is '
    'invented.\n'
    '- Themes are ordered by prevalence (most-raised first).\n'
    '- Recommendations are concrete and follow from the feedback.\n'
    '- The next-sprint signal reflects what members actually said (and is '
    'empty when no such signal was present).\n\n'
    'FAIL when any of those is violated (a hallucinated theme not in the '
    'input, mis-ordered themes, vague/unsupported recommendations, or a '
    'fabricated next-sprint signal).'
)

RUBRICS = {
    'onboarding': ONBOARDING_RUBRIC,
    'feedback': FEEDBACK_RUBRIC,
}


def rubric_for(callable_name):
    """Return the correctness rubric for the given assistant."""
    try:
        return RUBRICS[callable_name]
    except KeyError:
        raise LLMError(f'No judge rubric for callable {callable_name!r}') from None


def _build_judge_messages(callable_name, scenario_input, parsed_output):
    """Render the single user message carrying input + assistant output."""
    rubric = rubric_for(callable_name)
    input_json = json.dumps(scenario_input, indent=2, ensure_ascii=False, default=str)
    output_json = json.dumps(parsed_output, indent=2, ensure_ascii=False, default=str)
    content = (
        f'{rubric}\n\n'
        '=== SCENARIO INPUT (what the assistant was given) ===\n'
        f'{input_json}\n\n'
        "=== ASSISTANT OUTPUT (what it produced) ===\n"
        f'{output_json}\n\n'
        'Reason step by step about the rubric, then record your verdict by '
        f'calling the "{_TOOL_NAME}" tool.'
    )
    return [{'role': 'user', 'content': content}]


JUDGE_SYSTEM_PROMPT = (
    'You are a careful, consistent evaluation judge. You grade an AI '
    "assistant's output against a fixed rubric on a single PASS/FAIL "
    'correctness dimension. Reason before you label. Be strict and '
    'grounded: only mark PASS when the rubric is satisfied, and prefer FAIL '
    'when in doubt about a hallucination, a scope violation, or a leaked '
    'internal persona name. Your verdict must be the structured tool call.'
)


def run_judge(callable_name, scenario_input, parsed_output, *, model=None):
    """Run the judge over one scenario output and return its verdict.

    Args:
        callable_name: ``onboarding`` or ``feedback`` (selects the rubric).
        scenario_input: the JSON-able fixture input the assistant saw.
        parsed_output: the assistant's ``parsed_output`` (a JSON-able dict
            captured by :class:`FileTraceSink`), or ``None`` if the
            callable produced no structured output.
        model: optional model override passed through to ``llm.complete``.

    Returns:
        ``(JudgeVerdict, latency_seconds, raw_result)`` where ``raw_result``
        is the #799 ``LLMResult`` (for defensive usage/cost capture).

    Raises:
        LLMError: when the judge call fails or its output cannot validate.
    """
    tool = {
        'name': _TOOL_NAME,
        'description': 'Record the structured pass/fail verdict with reasoning.',
        'input_schema': JudgeVerdict.model_json_schema(),
    }
    messages = _build_judge_messages(callable_name, scenario_input, parsed_output)

    started = time.monotonic()
    result = llm.complete(
        messages,
        system=JUDGE_SYSTEM_PROMPT,
        model=model,
        tools=[tool],
        tool_choice={'type': 'tool', 'name': _TOOL_NAME},
    )
    latency_seconds = time.monotonic() - started

    if result.tool_input is None:
        raise LLMError('Judge did not return a structured verdict.')
    try:
        verdict = JudgeVerdict.model_validate(result.tool_input)
    except Exception as exc:
        raise LLMError(f'Judge returned an invalid verdict: {exc}') from None
    return verdict, latency_seconds, result


__all__ = [
    'JUDGE_PROMPT_VERSION',
    'JudgeLabel',
    'JudgeVerdict',
    'RUBRICS',
    'rubric_for',
    'run_judge',
    'JUDGE_SYSTEM_PROMPT',
]
