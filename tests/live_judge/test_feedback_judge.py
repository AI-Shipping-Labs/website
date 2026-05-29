"""Live LLM-judge scenarios for the feedback synthesizer (issue #811).

One test per row of the feedback criteria table. Each non-empty scenario
calls ``synthesize_feedback`` against the REAL provider, then asserts the
listed plain-English criteria via the LLM judge. The empty-feedback case
is a deterministic guard: it asserts ``FeedbackSynthesisEmpty`` is raised
WITHOUT any LLM call (no judge, no cost).

The whole module skips (no live call) when the LLM is not configured -- see
``conftest.py`` ``pytest_collection_modifyitems``.
"""

from unittest import mock

import pytest

from integrations.services import feedback_synthesis
from integrations.services.feedback_synthesis import (
    FeedbackSynthesisEmpty,
    SprintFeedbackInput,
)

from .inputs import feedback_input
from .judge import assert_criteria

pytestmark = pytest.mark.live_judge


def test_themes_grounded_and_ordered_by_support():
    """Mixed real feedback -> themes ordered by support, all grounded."""
    feedback = feedback_input('dataset/prevalence-high.json')
    result = feedback_synthesis.synthesize_feedback(feedback)

    # Deterministic: themes must be ordered by supporting_count descending.
    counts = [theme.supporting_count for theme in result.themes]
    assert counts == sorted(counts, reverse=True), (
        f'Themes are not ordered by supporting_count descending: {counts}'
    )

    assert_criteria(
        result,
        [
            'The themes are ordered by supporting_count in descending order '
            '(most-raised first).',
            'Every theme is grounded in the submitted feedback; no theme is '
            'hallucinated or absent from the responses.',
            'The recurring complaint raised by multiple members (slow / '
            'long scoping) surfaces as a top theme.',
        ],
    )


def test_recommendations_are_specific_and_signal_grounded():
    """Rich feedback -> specific, traceable recommendations + grounded signal."""
    feedback = feedback_input('dataset/quality-rich.json')
    result = feedback_synthesis.synthesize_feedback(feedback)

    assert_criteria(
        result,
        [
            'The recommendations are specific and actionable (concrete next '
            'steps), not generic platitudes like "improve morale".',
            'Each recommendation traces back to a concrete point raised in '
            'the submitted feedback.',
            'The next_sprint_signal is drawn from the actual answers (for '
            'example the likelihood-to-return responses), not invented.',
        ],
    )


def test_empty_feedback_raises_without_llm_call():
    """Empty feedback -> FeedbackSynthesisEmpty, no judge, no cost, no call.

    Deterministic guard. We patch the LLM service's ``complete`` so that
    any call would fail the test, then assert ``FeedbackSynthesisEmpty`` is
    raised before it. No judge is invoked and nothing is recorded.
    """
    empty = SprintFeedbackInput(sprint_name='Empty Sprint', response_count=0)

    with mock.patch(
        'integrations.services.feedback_synthesis.llm.complete'
    ) as mocked_complete:
        with pytest.raises(FeedbackSynthesisEmpty):
            feedback_synthesis.synthesize_feedback(empty)

    mocked_complete.assert_not_called()
