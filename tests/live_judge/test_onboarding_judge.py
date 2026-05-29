"""Live LLM-judge scenarios for the onboarding interviewer (issue #811).

One test per row of the onboarding criteria table. Each calls
``run_onboarding_turn`` against the REAL provider, then asserts the listed
plain-English criteria via the LLM judge. The persona-name leak is checked
deterministically (substring) in addition to a judge criterion.

The whole module skips (no live call) when the LLM is not configured -- see
``conftest.py`` ``pytest_collection_modifyitems``.
"""

import pytest

from questionnaires import onboarding_ai

from .inputs import PERSONA_NAMES, msg, persona_catalog
from .judge import assert_criteria

pytestmark = pytest.mark.live_judge


def _assert_no_persona_leak(assistant_message):
    """Deterministic guard: no internal persona name in member-facing text."""
    for name in PERSONA_NAMES:
        assert name not in assistant_message, (
            f'Internal persona name {name!r} leaked into the member-facing '
            f'assistant message: {assistant_message!r}'
        )


def test_researcher_gets_production_gap_questions_no_persona_leak():
    """Researcher moving into AI -> Taylor-style production-gap questions."""
    result = onboarding_ai.run_onboarding_turn(
        [],
        member_message=(
            "I'm a researcher / data scientist moving into AI engineering. "
            "I'm strong on modeling and experiments but I've never deployed "
            'anything to production.'
        ),
        persona_catalog=persona_catalog(),
    )

    # Highest-value assertion, checked WITHOUT the judge first.
    _assert_no_persona_leak(result.assistant_message)

    assert_criteria(
        result,
        [
            'The assistant steers toward a production / deployment-gap '
            'direction (shipping, serving, deployment) appropriate for '
            'someone strong on modeling but who has never deployed.',
            'The assistant asks about shipping or closing the production '
            'gap rather than re-teaching modeling or core ML theory.',
            'The member-facing assistant message contains none of the '
            'internal persona names Alex, Priya, Sam, or Taylor.',
        ],
    )


def test_low_ai_comfort_engineer_gets_ai_specific_questions():
    """Senior backend engineer, low AI comfort -> AI-specific probing."""
    result = onboarding_ai.run_onboarding_turn(
        [],
        member_message=(
            "I'm a senior backend engineer. I'm very comfortable with "
            'distributed systems and APIs, but I have almost no hands-on '
            'AI/ML experience.'
        ),
        persona_catalog=persona_catalog(),
    )

    _assert_no_persona_leak(result.assistant_message)

    assert_criteria(
        result,
        [
            'The assistant probes AI-specific ground (for example RAG, '
            'agents, evaluation/evals, or model selection) rather than '
            'asking only generic software-engineering questions.',
            'The member-facing assistant message reveals none of the '
            'internal persona names Alex, Priya, Sam, or Taylor.',
        ],
    )


def test_sparse_answers_get_clarifying_followup_not_complete():
    """One-word answers -> a clarifying follow-up, interview stays open."""
    transcript = [
        msg(
            'assistant',
            'What is the one concrete thing you would like to have built by '
            'the end of the next 6 to 8 weeks?',
        ),
        msg('user', 'idk'),
        msg(
            'assistant',
            'No problem. Roughly how many hours per week could you commit?',
        ),
    ]
    result = onboarding_ai.run_onboarding_turn(
        transcript,
        member_message='yeah',
        persona_catalog=persona_catalog(),
    )

    # Deterministic: sparse input must not complete the interview.
    assert result.is_complete is False
    _assert_no_persona_leak(result.assistant_message)

    assert_criteria(
        result,
        [
            'The assistant asks a concrete clarifying follow-up question to '
            'draw out a real answer, rather than inventing or assuming '
            'details the member never stated.',
            'The assistant does not fabricate a goal, time commitment, or '
            'project from the one-word answers.',
        ],
    )


def test_off_topic_message_is_redirected():
    """Off-topic message -> polite redirect, no derailment."""
    transcript = [
        msg(
            'assistant',
            'What is the one concrete outcome you want in the next 6 to 8 '
            'weeks?',
        ),
    ]
    result = onboarding_ai.run_onboarding_turn(
        transcript,
        member_message="What's the weather in Berlin?",
        persona_catalog=persona_catalog(),
    )

    _assert_no_persona_leak(result.assistant_message)

    assert_criteria(
        result,
        [
            'The assistant politely redirects the conversation back to '
            'onboarding (goals, time, blockers, project scope) instead of '
            'answering the unrelated weather question.',
            'The assistant does not follow the off-topic tangent or provide '
            'a weather forecast.',
        ],
    )


def test_completed_interview_yields_valid_grounded_extraction():
    """Full transcript -> completion with a valid, grounded extraction."""
    transcript = [
        msg(
            'assistant',
            'What is the one concrete thing you want to have built by the '
            'end of the next 6 to 8 weeks?',
        ),
        msg(
            'user',
            'I want to ship a small RAG-based customer support assistant '
            'that I can actually deploy and demo.',
        ),
        msg(
            'assistant',
            'Great, concrete goal. How many hours per week can you commit, '
            'consistently?',
        ),
        msg(
            'user',
            'About 10 hours a week, fairly steady across the week.',
        ),
        msg(
            'assistant',
            'What is the single biggest thing that tends to block your '
            'consistency?',
        ),
        msg(
            'user',
            'Scoping. I tend to over-scope and never finish. I am an '
            'experienced backend engineer but newer to AI.',
        ),
        msg(
            'assistant',
            'Thanks. What would "done and worthwhile" look like for this '
            'project?',
        ),
    ]
    result = onboarding_ai.run_onboarding_turn(
        transcript,
        member_message=(
            'A deployed RAG support assistant answering real questions, with '
            'a short demo video. That is enough for me to call it done.'
        ),
        persona_catalog=persona_catalog(),
    )

    _assert_no_persona_leak(result.assistant_message)

    # Deterministic structural checks once the interview completes.
    assert result.is_complete is True, (
        'Expected the interview to complete after a full transcript with a '
        'concrete goal, time commitment, blocker, and target outcome.'
    )
    extraction = result.extraction
    assert extraction is not None
    assert isinstance(extraction.persona_signal, onboarding_ai.PersonaSignal)
    assert 1 <= extraction.eng_comfort <= 5
    assert 1 <= extraction.ai_comfort <= 5
    assert extraction.primary_goal.strip()
    assert extraction.time_commitment_hours_per_week >= 0

    assert_criteria(
        result,
        [
            'The extracted primary_goal reflects the RAG-based support '
            'assistant the member described, not an invented goal.',
            'The extracted time_commitment_hours_per_week is consistent '
            'with the roughly 10 hours per week the member stated.',
            'The extraction does not invent facts the member never stated.',
        ],
    )
