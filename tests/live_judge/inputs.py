"""Scenario input builders for the live-judge set (issue #811).

Reuses the #809 fixtures and the ``ai_eval`` adapter helpers to assemble
the inputs each scenario feeds to the callable under test. Keeping the
fixture loading here lets the scenario tests read as user stories.
"""

from pathlib import Path

from integrations.services.ai_eval import runner
from questionnaires import onboarding_ai

FIXTURE_DIR = (
    Path(__file__).resolve().parents[1]
    / 'integrations'
    / 'services'
    / 'ai_eval'
    / 'fixtures'
)
ONBOARDING_FIXTURES = FIXTURE_DIR / 'onboarding'
FEEDBACK_FIXTURES = FIXTURE_DIR / 'feedback'

# Internal persona names that must never leak to the member (deterministic
# substring guard). Sourced from the PersonaSignal enum.
PERSONA_NAMES = ('Alex', 'Priya', 'Sam', 'Taylor')


def persona_catalog():
    """The full member-safe persona catalog (no internal names).

    Built from the #809 ``mid_conversation`` fixture, extended to all four
    archetypes so the assistant can route across the persona space.
    """
    return [
        onboarding_ai.PersonaInfo(
            signal='alex',
            archetype='Experienced engineer new to AI',
            description='Strong software engineering, building AI/ML depth.',
            questions=[
                onboarding_ai.PersonaQuestion(
                    prompt=(
                        'What is the one concrete outcome you want by the end '
                        'of the next 6 to 8 weeks?'
                    ),
                    question_type='long_text',
                ),
                onboarding_ai.PersonaQuestion(
                    prompt=(
                        'How many hours per week can you realistically '
                        'commit, consistently?'
                    ),
                    question_type='number',
                ),
            ],
        ),
        onboarding_ai.PersonaInfo(
            signal='priya',
            archetype='Researcher / data scientist moving toward shipping',
            description='Strong ML, needs deployment and engineering practice.',
            questions=[
                onboarding_ai.PersonaQuestion(
                    prompt='Have you deployed an AI system to production before?',
                    question_type='long_text',
                ),
            ],
        ),
        onboarding_ai.PersonaInfo(
            signal='sam',
            archetype='Builder shipping AI features at work',
            description='Ships product features, wants depth on AI patterns.',
            questions=[
                onboarding_ai.PersonaQuestion(
                    prompt='What AI feature are you trying to ship?',
                    question_type='long_text',
                ),
            ],
        ),
        onboarding_ai.PersonaInfo(
            signal='taylor',
            archetype='Researcher / data scientist closing the production gap',
            description=(
                'Strong on modeling, needs deployment, serving, and '
                'production engineering.'
            ),
            questions=[
                onboarding_ai.PersonaQuestion(
                    prompt=(
                        'What is the gap between your modeling work and a '
                        'deployed system?'
                    ),
                    question_type='long_text',
                ),
            ],
        ),
    ]


def msg(role, content):
    """Build one transcript message dict."""
    return {'role': role, 'content': content}


def feedback_input(filename):
    """Load and validate a feedback fixture into a ``SprintFeedbackInput``."""
    data = runner.load_fixture(FEEDBACK_FIXTURES / filename)
    return runner.build_feedback_input(data, source=filename)
