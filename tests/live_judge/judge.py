"""LLM judge for the live scenario set (issue #811).

``assert_criteria(result, criteria)`` renders the AI output under test to
text, asks an LLM judge (built on the #799 service) to score each
plain-English criterion against it, records the call's usage/cost, and
asserts every criterion passed. A failed criterion raises ``AssertionError``
whose message carries both the criterion description AND the judge's
reasoning, so the failure explains itself.

Modeled on the documentation-agent reference ``judge.py`` but built on the
project's provider-neutral LLM service: it imports only from
``integrations.services.llm`` (never a vendor SDK) and uses the #799
structured-output tool path -- a Pydantic model doubles as the tool input
schema via ``model_json_schema()``, the model is forced to call that tool,
and ``Model.model_validate(result.tool_input)`` parses the verdict.

Structured output puts the reasoning (``judgement``) BEFORE the boolean
``passed`` label so the judge reasons before committing to a verdict.

Judge model selection: ``LLM_JUDGE_MODEL`` config, falling back to
``LLM_MODEL`` when unset (judge == assistant model, zero-config). The
resolved name is passed to ``llm.complete(..., model=...)``.

This module never imports or initializes Logfire (#813 owns that gating).
"""

import json

from pydantic import BaseModel, Field

from integrations.config import get_config
from integrations.services import llm
from integrations.services.llm import LLMError

from .cost_tracker import capture_usage

_TOOL_NAME = 'record_evaluation'

JUDGE_SYSTEM_PROMPT = (
    'You are an expert, careful judge evaluating the output of an AI '
    'assistant. You are given the assistant output (and any grounding '
    'context) plus a list of plain-English criteria. For each criterion, '
    'reason about the evidence in the output BEFORE deciding whether it '
    'passed. Be strict and grounded: only mark a criterion as passed when '
    'the evidence clearly supports it. Record your evaluation by calling '
    f'the "{_TOOL_NAME}" tool.'
)


class JudgeCriterion(BaseModel):
    """Evaluation of a single plain-English criterion.

    Field order is deliberate: ``judgement`` (the reasoning) is declared
    and filled BEFORE the boolean ``passed`` label, so the structured
    output carries chain-of-thought before the verdict.
    """

    criterion_description: str = Field(
        description='The specific criterion the output is evaluated against.',
    )
    judgement: str = Field(
        description=(
            'Clear reasoning about whether the output satisfies this '
            'criterion, citing specific evidence. Write this BEFORE the '
            'passed label.'
        ),
    )
    passed: bool = Field(
        description='True iff the output satisfies this criterion.',
    )


class JudgeFeedback(BaseModel):
    """The complete judge report across all criteria."""

    criteria: list[JudgeCriterion] = Field(
        description='One evaluation per supplied criterion.',
    )
    feedback: str = Field(
        description='Holistic summary of the output across all criteria.',
    )


def resolve_judge_model():
    """Return the judge model: ``LLM_JUDGE_MODEL`` or ``LLM_MODEL`` fallback."""
    judge_model = (get_config('LLM_JUDGE_MODEL', '') or '').strip()
    if judge_model:
        return judge_model
    return (get_config('LLM_MODEL', '') or '').strip() or None


_JUDGE_PROMPT_TEMPLATE = """
Evaluate the AI assistant's output against the following criteria. Score
each criterion independently.

<CRITERIA>
{criteria}
</CRITERIA>

The assistant's output (and any grounding context) was:
<ASSISTANT_OUTPUT>
{output}
</ASSISTANT_OUTPUT>

Reason about each criterion against the output above, then record your
evaluation by calling the "{tool_name}" tool with one entry per criterion.
""".strip()


def run_judge(output_text, criteria, *, model=None):
    """Run the LLM judge over an output and return a ``JudgeFeedback``.

    Args:
        output_text: the AI output under test rendered to text (assistant
            message / extraction / synthesis result plus any grounding).
        criteria: list of plain-English criteria strings.
        model: optional explicit judge model; resolved from config when
            omitted.

    Returns:
        ``(JudgeFeedback, raw_result)`` where ``raw_result`` is the #799
        ``LLMResult`` (for defensive usage/cost capture).

    Raises:
        LLMError: when the judge call fails or its output cannot validate.
    """
    judge_model = model or resolve_judge_model()
    tool = {
        'name': _TOOL_NAME,
        'description': 'Record the per-criterion evaluation with reasoning.',
        'input_schema': JudgeFeedback.model_json_schema(),
    }
    numbered = '\n'.join(f'{i}. {c}' for i, c in enumerate(criteria, start=1))
    messages = [
        {
            'role': 'user',
            'content': _JUDGE_PROMPT_TEMPLATE.format(
                criteria=numbered,
                output=output_text,
                tool_name=_TOOL_NAME,
            ),
        }
    ]

    result = llm.complete(
        messages,
        system=JUDGE_SYSTEM_PROMPT,
        model=judge_model,
        tools=[tool],
        tool_choice={'type': 'tool', 'name': _TOOL_NAME},
    )
    if result.tool_input is None:
        raise LLMError('Judge did not return a structured evaluation.')
    try:
        feedback = JudgeFeedback.model_validate(result.tool_input)
    except Exception as exc:
        raise LLMError(f'Judge returned an invalid evaluation: {exc}') from None
    return feedback, result


def render_output(result):
    """Render an AI callable's result to judge-readable text.

    Accepts a Pydantic model (it has ``model_dump_json``), a plain dict /
    str, or any object; falls back to ``str`` so the judge always sees
    something grounded.
    """
    if hasattr(result, 'model_dump_json'):
        return result.model_dump_json(indent=2)
    if isinstance(result, (dict, list)):
        return json.dumps(result, indent=2, ensure_ascii=False, default=str)
    return str(result)


def assert_criteria(result, criteria, *, model=None):
    """Judge ``result`` against ``criteria`` and assert every one passed.

    Renders ``result`` to text, runs the LLM judge, records the call's
    usage/cost (including the per-criterion pass tally), prints the judge's
    holistic feedback and per-criterion reasoning, then asserts each
    criterion passed.

    Raises:
        AssertionError: on the first failed criterion. The message
            contains both the ``criterion_description`` and the judge's
            ``judgement`` so the failure explains itself.
    """
    output_text = render_output(result)
    feedback, raw_result = run_judge(output_text, criteria, model=model)

    judge_model = model or resolve_judge_model()
    passed_count = sum(1 for c in feedback.criteria if c.passed)
    capture_usage(
        judge_model,
        raw_result,
        criteria_total=len(feedback.criteria),
        criteria_passed=passed_count,
    )

    print('judge feedback:')
    print(feedback.feedback)
    for criterion in feedback.criteria:
        status = 'PASS' if criterion.passed else 'FAIL'
        print(f'[{status}] {criterion.criterion_description}: {criterion.judgement}')

    for criterion in feedback.criteria:
        assert criterion.passed, (
            f'{criterion.criterion_description}: {criterion.judgement}'
        )

    return feedback


__all__ = [
    'JudgeCriterion',
    'JudgeFeedback',
    'JUDGE_SYSTEM_PROMPT',
    'assert_criteria',
    'render_output',
    'resolve_judge_model',
    'run_judge',
]
