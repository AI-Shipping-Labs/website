"""Tests for the pure first-sprint draft callable (issue #1205)."""

import inspect
import sys
from unittest.mock import patch

from django.test import SimpleTestCase

from integrations.services.llm import LLMError, LLMResult
from plans.services.first_sprint_draft import (
    DraftResource,
    DraftWeek,
    FirstSprintDraftInput,
    FirstSprintDraftResult,
    FirstSprintDraftUnavailable,
    OnboardingAnswer,
    draft_first_sprint,
)


def _tool_input(weeks=2):
    return {
        'title': 'First sprint',
        'goal': 'Ship a portfolio project',
        'summary_current_situation': 'Learning AI app development.',
        'summary_goal': 'Build and publish a small useful app.',
        'summary_main_gap': 'Needs scope and weekly checkpoints.',
        'summary_weekly_hours': '~5 hours/week',
        'summary_why_this_plan': 'Matches onboarding goals.',
        'focus_main': 'Build one small end-to-end project.',
        'focus_supporting': ['Keep scope small'],
        'accountability': 'Post progress weekly.',
        'weeks': [
            {'week_number': n, 'theme': f'Week {n}', 'checkpoints': [f'CP {n}']}
            for n in range(1, weeks + 1)
        ],
        'resources': [{'title': 'Starter guide', 'url': '', 'note': 'Read'}],
        'deliverables': ['Demo app'],
        'next_steps': ['Pick project idea'],
        'internal_notes': 'Staff should verify scope.',
        'rationale': 'Onboarding asks for a portfolio artifact.',
    }


def _input(duration=2):
    return FirstSprintDraftInput(
        member_label='member@test.com',
        sprint_name='July sprint',
        sprint_duration_weeks=duration,
        persona='Sam',
        onboarding_answers=[
            OnboardingAnswer(prompt='Goal?', answer='Build AI projects'),
        ],
    )


class FirstSprintDraftCallableTest(SimpleTestCase):
    def test_returns_validated_result(self):
        with patch(
            'plans.services.first_sprint_draft.llm.is_enabled',
            return_value=True,
        ), patch(
            'plans.services.first_sprint_draft.llm.complete',
            return_value=LLMResult(tool_input=_tool_input()),
        ):
            result = draft_first_sprint(_input())

        self.assertIsInstance(result, FirstSprintDraftResult)
        self.assertEqual(result.goal, 'Ship a portfolio project')
        self.assertEqual(result.weeks[0].week_number, 1)
        self.assertIsInstance(result.weeks[0], DraftWeek)
        self.assertIsInstance(result.resources[0], DraftResource)

    def test_rejects_wrong_week_count(self):
        with patch(
            'plans.services.first_sprint_draft.llm.is_enabled',
            return_value=True,
        ), patch(
            'plans.services.first_sprint_draft.llm.complete',
            return_value=LLMResult(tool_input=_tool_input(weeks=1)),
        ):
            with self.assertRaises(LLMError):
                draft_first_sprint(_input(duration=2))

    def test_disabled_raises_without_calling_llm(self):
        with patch(
            'plans.services.first_sprint_draft.llm.is_enabled',
            return_value=False,
        ), patch('plans.services.first_sprint_draft.llm.complete') as complete:
            with self.assertRaises(FirstSprintDraftUnavailable):
                draft_first_sprint(_input())
        complete.assert_not_called()

    def test_module_stays_django_independent(self):
        from plans.services import first_sprint_draft

        source = inspect.getsource(first_sprint_draft)
        for forbidden in ('django.', 'plans.models', 'questionnaires.'):
            self.assertNotIn(forbidden, source)
        self.assertIn('plans.services.first_sprint_draft', sys.modules)
