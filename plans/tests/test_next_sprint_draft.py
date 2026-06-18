"""Tests for the Django-independent next-sprint draft callable (#891).

The LLM is mocked at the service boundary in every test (CI never hits a
live provider). Covers the callable contract, the disabled gate, result
validation (including empty recent_updates), and Django-independence.
"""

from unittest.mock import patch

from django.test import SimpleTestCase

from integrations.services.llm import LLMError, LLMResult
from plans.services.next_sprint_draft import (
    NextSprintDraftInput,
    NextSprintDraftResult,
    NextSprintDraftUnavailable,
    OnboardingAnswer,
    RecentActivity,
    RecentUpdate,
    _build_user_message,
    draft_next_sprint,
)

_VALID_TOOL_INPUT = {
    'summary_current_situation': 'Shipped a first RAG prototype.',
    'summary_goal': 'Turn the prototype into an evaluated pipeline.',
    'summary_main_gap': 'No eval harness yet; retrieval is untested.',
    'summary_weekly_hours': '~6 hours/week',
    'goal': 'Ship an evaluated RAG pipeline',
    'suggested_next_steps': [
        'Add a small eval set',
        'Wire retrieval metrics',
    ],
    'rationale': 'Updates show retrieval works but quality is unmeasured.',
}


def _input(recent_updates=None):
    return NextSprintDraftInput(
        member_label='m@test.com',
        current_sprint_name='May Cohort',
        next_sprint_name='June Cohort',
        next_sprint_duration_weeks=6,
        goal='Build a RAG prototype',
        summary_current_situation='Started exploring RAG.',
        summary_main_gap='Not deployed yet.',
        not_done_checkpoints=['Deploy the prototype'],
        done_deliverables=['Working notebook'],
        recent_updates=recent_updates if recent_updates is not None else [
            RecentUpdate(
                author_display='Member',
                posted_at='2026-06-01T10:00:00+00:00',
                text='Got retrieval working but no evals yet.',
            ),
        ],
    )


class DraftContractTest(SimpleTestCase):
    def test_returns_validated_result_from_tool_input(self):
        with patch(
            'plans.services.next_sprint_draft.llm.is_enabled',
            return_value=True,
        ), patch(
            'plans.services.next_sprint_draft.llm.complete',
            return_value=LLMResult(tool_input=dict(_VALID_TOOL_INPUT)),
        ):
            result = draft_next_sprint(_input())

        self.assertIsInstance(result, NextSprintDraftResult)
        self.assertEqual(result.goal, 'Ship an evaluated RAG pipeline')
        self.assertEqual(
            result.summary_current_situation, 'Shipped a first RAG prototype.',
        )
        self.assertEqual(
            result.suggested_next_steps,
            ['Add a small eval set', 'Wire retrieval metrics'],
        )
        self.assertTrue(result.rationale)

    def test_passes_tool_and_tool_choice_using_model_schema(self):
        schema = NextSprintDraftResult.model_json_schema()
        with patch(
            'plans.services.next_sprint_draft.llm.is_enabled',
            return_value=True,
        ), patch(
            'plans.services.next_sprint_draft.llm.complete',
            return_value=LLMResult(tool_input=dict(_VALID_TOOL_INPUT)),
        ) as mock_complete:
            draft_next_sprint(_input())

        _, kwargs = mock_complete.call_args
        self.assertEqual(len(kwargs['tools']), 1)
        self.assertEqual(kwargs['tools'][0]['input_schema'], schema)
        self.assertEqual(kwargs['tool_choice']['type'], 'tool')
        self.assertEqual(
            kwargs['tool_choice']['name'], kwargs['tools'][0]['name'],
        )

    def test_empty_recent_updates_still_validates(self):
        with patch(
            'plans.services.next_sprint_draft.llm.is_enabled',
            return_value=True,
        ), patch(
            'plans.services.next_sprint_draft.llm.complete',
            return_value=LLMResult(tool_input=dict(_VALID_TOOL_INPUT)),
        ) as mock_complete:
            result = draft_next_sprint(_input(recent_updates=[]))

        self.assertIsInstance(result, NextSprintDraftResult)
        # The user message must note the absence of updates so the model
        # leans on plan state only.
        user_message = mock_complete.call_args.args[0][0]['content']
        self.assertIn('no recent updates', user_message)


class DraftGatingTest(SimpleTestCase):
    def test_disabled_raises_unavailable_without_calling_complete(self):
        with patch(
            'plans.services.next_sprint_draft.llm.is_enabled',
            return_value=False,
        ), patch(
            'plans.services.next_sprint_draft.llm.complete',
        ) as mock_complete:
            with self.assertRaises(NextSprintDraftUnavailable):
                draft_next_sprint(_input())
        mock_complete.assert_not_called()

    def test_unavailable_is_an_llm_error_subclass(self):
        self.assertTrue(issubclass(NextSprintDraftUnavailable, LLMError))


class DraftErrorTest(SimpleTestCase):
    def test_llm_error_propagates(self):
        with patch(
            'plans.services.next_sprint_draft.llm.is_enabled',
            return_value=True,
        ), patch(
            'plans.services.next_sprint_draft.llm.complete',
            side_effect=LLMError('boom'),
        ):
            with self.assertRaises(LLMError):
                draft_next_sprint(_input())

    def test_missing_tool_input_raises_llm_error(self):
        with patch(
            'plans.services.next_sprint_draft.llm.is_enabled',
            return_value=True,
        ), patch(
            'plans.services.next_sprint_draft.llm.complete',
            return_value=LLMResult(tool_input=None),
        ):
            with self.assertRaises(LLMError):
                draft_next_sprint(_input())


class ProfileBlockRenderingTest(SimpleTestCase):
    """The ``=== Member profile ===`` block in ``_build_user_message`` (#913)."""

    def _profiled_input(self):
        return NextSprintDraftInput(
            member_label='m@test.com',
            next_sprint_name='June Cohort',
            goal='Build a RAG prototype',
            persona='Sam — Technical Professional',
            crm_summary='Strong engineer, needs a portfolio piece.',
            crm_next_steps='Ship a RAG project this sprint.',
            onboarding_answers=[
                OnboardingAnswer(
                    prompt='What are your goals?',
                    answer='Switch into an AI engineering role',
                ),
                OnboardingAnswer(
                    prompt='Background?', answer='Ten years of backend Java',
                ),
            ],
            recent_activity=[
                RecentActivity(
                    occurred_at='2026-06-01',
                    category='Learning',
                    type_label='Lesson',
                    label='Opened lesson: Agents basics',
                ),
            ],
        )

    def test_profile_block_rendered_before_plan_state(self):
        message = _build_user_message(self._profiled_input())

        self.assertIn('=== Member profile ===', message)
        self.assertIn('Persona: Sam — Technical Professional', message)
        self.assertIn(
            'CRM summary: Strong engineer, needs a portfolio piece.', message,
        )
        self.assertIn('CRM next steps: Ship a RAG project this sprint.', message)
        self.assertIn(
            '  - What are your goals?: Switch into an AI engineering role',
            message,
        )
        self.assertIn('  - Background?: Ten years of backend Java', message)
        self.assertIn('Recent activity:', message)
        self.assertIn(
            '  - 2026-06-01 [Learning] Lesson: Opened lesson: Agents basics',
            message,
        )
        # Positioned before the current-plan-state block.
        self.assertLess(
            message.index('=== Member profile ==='),
            message.index('=== Current plan state ==='),
        )

    def test_no_profile_block_when_all_fields_empty(self):
        message = _build_user_message(
            NextSprintDraftInput(member_label='m@test.com', goal='Do a thing'),
        )

        self.assertNotIn('=== Member profile ===', message)
        self.assertNotIn('Persona:', message)
        # The rest of the message is still intact.
        self.assertIn('=== Current plan state ===', message)
        self.assertIn('=== Recent #plan-sprints updates (newest first) ===', message)

    def test_only_set_profile_subsections_are_rendered(self):
        message = _build_user_message(
            NextSprintDraftInput(persona='Sam — Technical Professional'),
        )

        self.assertIn('=== Member profile ===', message)
        self.assertIn('Persona: Sam — Technical Professional', message)
        self.assertNotIn('CRM summary:', message)
        self.assertNotIn('CRM next steps:', message)
        self.assertNotIn('Onboarding answers:', message)
        self.assertNotIn('Recent activity:', message)

    def test_recent_activity_alone_renders_profile_block(self):
        message = _build_user_message(
            NextSprintDraftInput(
                recent_activity=[
                    RecentActivity(
                        occurred_at='2026-06-02',
                        category='Events',
                        type_label='Joined',
                        label='Joined event: Sprint kickoff',
                    ),
                ],
            ),
        )

        self.assertIn('=== Member profile ===', message)
        self.assertIn('Recent activity:', message)
        self.assertIn(
            '  - 2026-06-02 [Events] Joined: Joined event: Sprint kickoff',
            message,
        )


class DraftImportIsolationTest(SimpleTestCase):
    """The draft module must stay Django-independent (mirrors #805)."""

    def test_module_source_imports_no_django_or_app_models(self):
        import inspect

        from plans.services import next_sprint_draft

        source = inspect.getsource(next_sprint_draft)
        forbidden = [
            'from django.db',
            'import django.db',
            'from django.http',
            'from plans.models',
            'import plans.models',
            'from crm',
            'import crm',
            'HttpRequest',
        ]
        for needle in forbidden:
            self.assertNotIn(
                needle, source,
                f'next_sprint_draft must not reference {needle!r}',
            )

    def test_loaded_module_has_no_django_db_dependency(self):
        import sys

        from plans.services import next_sprint_draft

        module_globals = vars(next_sprint_draft)
        self.assertNotIn('models', module_globals)
        self.assertNotIn('HttpRequest', module_globals)
        self.assertIn('plans.services.next_sprint_draft', sys.modules)
