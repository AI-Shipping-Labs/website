"""Tests for the shared next-sprint draft orchestration (#891).

``draft_next_sprint_plan`` composes carry-over (#808) with the LLM draft
callable. The LLM is stubbed at the service boundary. Covers carry-over
composition, no-source-plan, draft persistence with the plan left
untouched, regenerate-overwrites, LLM-off, and LLM-failure (no partial
draft row).
"""

import datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from analytics.models import UserActivity
from crm.models import CRMRecord
from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting
from integrations.services.llm import LLMError
from plans.models import (
    Checkpoint,
    Deliverable,
    NextSprintPlanDraft,
    NextStep,
    Plan,
    Sprint,
    Week,
)
from plans.services import draft_next_sprint_plan
from plans.services.next_sprint_draft import NextSprintDraftResult
from plans.services.next_sprint_draft_service import _build_draft_input
from questionnaires.models import (
    Answer,
    Questionnaire,
    Response,
    ResponseQuestion,
)

User = get_user_model()


def _onboarding_response(member, *, qa):
    """Create a submitted onboarding response from ``(prompt, text)`` tuples.

    A ``text`` of ``None`` leaves the question unanswered (no ``Answer``
    row), mirroring a member who skipped a question.
    """
    response = Response.objects.create(
        questionnaire=Questionnaire.objects.get(slug='onboarding-general'),
        respondent=member,
        status='submitted',
    )
    for order, (prompt, text) in enumerate(qa):
        rq = ResponseQuestion.objects.create(
            response=response, question_type='long_text',
            prompt=prompt, order=order,
        )
        if text is not None:
            Answer.objects.create(response=response, question=rq, text_value=text)
    return response


def _draft_result(goal='Ship the next thing'):
    return NextSprintDraftResult(
        summary_current_situation='Now',
        summary_goal='Goal',
        summary_main_gap='Gap',
        summary_weekly_hours='~6h',
        goal=goal,
        suggested_next_steps=['Step one'],
        rationale='Because.',
    )


def _make_plan(member, sprint, weeks=4):
    plan = Plan.objects.create(member=member, sprint=sprint)
    for n in range(1, weeks + 1):
        Week.objects.create(plan=plan, week_number=n, position=n - 1)
    return plan


class DraftServiceTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.member = User.objects.create_user(
            email='m@test.com', password='pw',
        )
        cls.s_may = Sprint.objects.create(
            name='May', slug='may', start_date=datetime.date(2026, 5, 1),
        )
        cls.s_jun = Sprint.objects.create(
            name='Jun', slug='jun', start_date=datetime.date(2026, 6, 1),
        )

    def _enable_llm(self, draft=None):
        """Patch the service so the LLM is on and returns ``draft``."""
        return (
            patch(
                'plans.services.next_sprint_draft_service.llm.is_enabled',
                return_value=True,
            ),
            patch(
                'plans.services.next_sprint_draft_service.draft_next_sprint',
                return_value=draft or _draft_result(),
            ),
        )

    def test_carry_over_composition_copies_unfinished(self):
        source = _make_plan(self.member, self.s_may)
        first_week = source.weeks.order_by('week_number').first()
        Checkpoint.objects.create(week=first_week, description='Cp A', position=0)
        Deliverable.objects.create(plan=source, description='Del B', position=0)
        NextStep.objects.create(plan=source, description='Step C', position=0)
        dest = _make_plan(self.member, self.s_jun)

        enable, stub = self._enable_llm()
        with enable, stub:
            outcome = draft_next_sprint_plan(
                destination_plan=dest, actor=self.staff,
            )

        self.assertEqual(outcome['carried_over'], 3)
        self.assertEqual(outcome['source_plan'].pk, source.pk)
        # The three unfinished items are now real rows on the destination.
        self.assertEqual(
            Checkpoint.objects.filter(week__plan=dest).count(), 1,
        )
        self.assertEqual(dest.deliverables.count(), 1)
        self.assertEqual(dest.next_steps.count(), 1)
        self.assertEqual(dest.next_steps.get().kind, 'pre_sprint')

    def test_no_source_plan_runs_without_error_and_drafts(self):
        dest = _make_plan(self.member, self.s_may)

        enable, stub = self._enable_llm()
        with enable, stub:
            outcome = draft_next_sprint_plan(
                destination_plan=dest, actor=self.staff,
            )

        self.assertEqual(outcome['carried_over'], 0)
        self.assertIsNone(outcome['source_plan'])
        # LLM on -> a draft from current state only.
        self.assertEqual(NextSprintPlanDraft.objects.filter(plan=dest).count(), 1)

    def test_draft_persisted_plan_fields_untouched(self):
        dest = _make_plan(self.member, self.s_jun)
        dest.goal = 'Original goal'
        dest.summary_goal = 'Original summary'
        dest.save(update_fields=['goal', 'summary_goal'])

        enable, stub = self._enable_llm(_draft_result(goal='Drafted goal'))
        with enable, stub:
            draft_next_sprint_plan(destination_plan=dest, actor=self.staff)

        draft = NextSprintPlanDraft.objects.get(plan=dest)
        self.assertEqual(draft.result_json['goal'], 'Drafted goal')
        # The plan's live fields are NOT touched by the draft step.
        dest.refresh_from_db()
        self.assertEqual(dest.goal, 'Original goal')
        self.assertEqual(dest.summary_goal, 'Original summary')

    def test_regenerate_overwrites_single_row(self):
        dest = _make_plan(self.member, self.s_jun)

        enable1, stub1 = self._enable_llm(_draft_result(goal='First'))
        with enable1, stub1:
            draft_next_sprint_plan(destination_plan=dest, actor=self.staff)
        enable2, stub2 = self._enable_llm(_draft_result(goal='Second'))
        with enable2, stub2:
            draft_next_sprint_plan(destination_plan=dest, actor=self.staff)

        drafts = NextSprintPlanDraft.objects.filter(plan=dest)
        self.assertEqual(drafts.count(), 1)
        self.assertEqual(drafts.first().result_json['goal'], 'Second')

    def test_llm_off_runs_carry_over_writes_no_draft(self):
        source = _make_plan(self.member, self.s_may)
        first_week = source.weeks.order_by('week_number').first()
        Checkpoint.objects.create(week=first_week, description='Cp A', position=0)
        dest = _make_plan(self.member, self.s_jun)

        with patch(
            'plans.services.next_sprint_draft_service.llm.is_enabled',
            return_value=False,
        ):
            outcome = draft_next_sprint_plan(
                destination_plan=dest, actor=self.staff,
            )

        self.assertEqual(outcome['carried_over'], 1)
        self.assertFalse(outcome['llm_enabled'])
        self.assertIsNone(outcome['draft'])
        self.assertEqual(NextSprintPlanDraft.objects.filter(plan=dest).count(), 0)

    def test_llm_off_compacts_carry_over_before_draft_handling(self):
        source = _make_plan(self.member, self.s_may)
        Checkpoint.objects.create(
            week=source.weeks.get(week_number=3),
            description='Late Cp',
            position=0,
        )
        dest = _make_plan(self.member, self.s_jun)

        with patch(
            'plans.services.next_sprint_draft_service.llm.is_enabled',
            return_value=False,
        ):
            outcome = draft_next_sprint_plan(
                destination_plan=dest, actor=self.staff,
            )

        self.assertEqual(outcome['carried_over'], 1)
        self.assertFalse(outcome['llm_enabled'])
        self.assertEqual(
            [
                c.description
                for c in dest.weeks.get(week_number=1).checkpoints.all()
            ],
            ['Late Cp'],
        )
        self.assertEqual(dest.weeks.get(week_number=3).checkpoints.count(), 0)
        self.assertEqual(NextSprintPlanDraft.objects.filter(plan=dest).count(), 0)

    def test_llm_failure_leaves_carry_over_and_no_partial_draft(self):
        source = _make_plan(self.member, self.s_may)
        first_week = source.weeks.order_by('week_number').first()
        Checkpoint.objects.create(week=first_week, description='Cp A', position=0)
        dest = _make_plan(self.member, self.s_jun)

        with patch(
            'plans.services.next_sprint_draft_service.llm.is_enabled',
            return_value=True,
        ), patch(
            'plans.services.next_sprint_draft_service.draft_next_sprint',
            side_effect=LLMError('boom'),
        ):
            outcome = draft_next_sprint_plan(
                destination_plan=dest, actor=self.staff,
            )

        # Carry-over committed before the draft attempt.
        self.assertEqual(outcome['carried_over'], 1)
        self.assertTrue(outcome['draft_error'])
        self.assertEqual(
            Checkpoint.objects.filter(week__plan=dest).count(), 1,
        )
        # No partial draft row.
        self.assertEqual(NextSprintPlanDraft.objects.filter(plan=dest).count(), 0)


class DraftProfileInjectionTest(TestCase):
    """Member-profile injection into the shared draft path (#913).

    The profile is assembled by the #883 ``build_member_profile_context``
    and mapped onto the plain ``NextSprintDraftInput`` fields. The LLM is
    stubbed at the service boundary in every test.
    """

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff913@test.com', password='pw', is_staff=True,
        )
        cls.s_jun = Sprint.objects.create(
            name='Jun', slug='jun-913', start_date=datetime.date(2026, 6, 1),
        )

    def tearDown(self):
        IntegrationSetting.objects.filter(
            key='NEXT_SPRINT_DRAFT_USE_PROFILE',
        ).delete()
        clear_config_cache()

    def _profiled_member(self, email):
        member = User.objects.create_user(email=email, password='pw')
        _onboarding_response(member, qa=[
            ('What are your goals?', 'Switch into an AI engineering role'),
            ('Background?', 'Ten years of backend Java'),
        ])
        CRMRecord.objects.create(
            user=member, persona='Sam — Technical Professional',
            summary='Strong engineer, needs a portfolio piece.',
            next_steps='Ship a RAG project this sprint.',
        )
        UserActivity.objects.filter(user=member).delete()
        UserActivity.objects.create(
            user=member,
            event_type=UserActivity.EVENT_LESSON_OPEN,
            label='Opened lesson: Agents basics',
            occurred_at=timezone.now(),
        )
        return member

    def _enable_llm(self, draft=None):
        return (
            patch(
                'plans.services.next_sprint_draft_service.llm.is_enabled',
                return_value=True,
            ),
            patch(
                'plans.services.next_sprint_draft_service.draft_next_sprint',
                return_value=draft or _draft_result(),
            ),
        )

    def test_profile_fields_flow_into_draft_input(self):
        member = self._profiled_member('profiled@test.com')
        dest = _make_plan(member, self.s_jun)

        draft_input = _build_draft_input(
            destination_plan=dest, source_plan=None, recent_updates=[],
        )

        self.assertEqual(draft_input.persona, 'Sam — Technical Professional')
        self.assertEqual(
            draft_input.crm_summary, 'Strong engineer, needs a portfolio piece.',
        )
        self.assertEqual(
            draft_input.crm_next_steps, 'Ship a RAG project this sprint.',
        )
        answers = {a.prompt: a.answer for a in draft_input.onboarding_answers}
        self.assertEqual(
            answers['What are your goals?'],
            'Switch into an AI engineering role',
        )
        self.assertEqual(answers['Background?'], 'Ten years of backend Java')
        self.assertEqual(len(draft_input.recent_activity), 1)
        self.assertEqual(
            draft_input.recent_activity[0].label,
            'Opened lesson: Agents basics',
        )
        self.assertEqual(draft_input.recent_activity[0].category, 'Learning')

    def test_profile_block_rendered_in_user_message_via_service(self):
        from plans.services.next_sprint_draft import _build_user_message

        member = self._profiled_member('profiled2@test.com')
        dest = _make_plan(member, self.s_jun)

        draft_input = _build_draft_input(
            destination_plan=dest, source_plan=None, recent_updates=[],
        )
        message = _build_user_message(draft_input)

        self.assertIn('=== Member profile ===', message)
        self.assertIn('Persona: Sam — Technical Professional', message)
        self.assertIn('Recent activity:', message)
        self.assertIn('Opened lesson: Agents basics', message)
        self.assertLess(
            message.index('=== Member profile ==='),
            message.index('=== Current plan state ==='),
        )

    def test_unanswered_onboarding_questions_excluded(self):
        member = User.objects.create_user(email='partial@test.com', password='pw')
        _onboarding_response(member, qa=[
            ('What are your goals?', 'Become an AI engineer'),
            ('Anything else?', None),
        ])
        dest = _make_plan(member, self.s_jun)

        draft_input = _build_draft_input(
            destination_plan=dest, source_plan=None, recent_updates=[],
        )

        prompts = {a.prompt for a in draft_input.onboarding_answers}
        self.assertIn('What are your goals?', prompts)
        self.assertNotIn('Anything else?', prompts)

    def test_member_with_no_profile_still_drafts(self):
        member = User.objects.create_user(email='blank@test.com', password='pw')
        UserActivity.objects.filter(user=member).delete()
        dest = _make_plan(member, self.s_jun)

        enable, stub = self._enable_llm()
        with enable, stub:
            draft_next_sprint_plan(destination_plan=dest, actor=self.staff)

        self.assertEqual(NextSprintPlanDraft.objects.filter(plan=dest).count(), 1)
        # Empty profile fields -> no profile block in the rendered message.
        draft_input = _build_draft_input(
            destination_plan=dest, source_plan=None, recent_updates=[],
        )
        self.assertEqual(draft_input.persona, '')
        self.assertEqual(draft_input.crm_summary, '')
        self.assertEqual(draft_input.onboarding_answers, [])
        self.assertEqual(draft_input.recent_activity, [])

    def test_gate_off_omits_profile_from_input(self):
        member = self._profiled_member('gated@test.com')
        dest = _make_plan(member, self.s_jun)

        IntegrationSetting.objects.update_or_create(
            key='NEXT_SPRINT_DRAFT_USE_PROFILE',
            defaults={'value': 'false'},
        )
        clear_config_cache()

        draft_input = _build_draft_input(
            destination_plan=dest, source_plan=None, recent_updates=[],
        )

        self.assertEqual(draft_input.persona, '')
        self.assertEqual(draft_input.crm_summary, '')
        self.assertEqual(draft_input.crm_next_steps, '')
        self.assertEqual(draft_input.onboarding_answers, [])
        self.assertEqual(draft_input.recent_activity, [])

        from plans.services.next_sprint_draft import _build_user_message
        self.assertNotIn('=== Member profile ===', _build_user_message(draft_input))

    def test_shared_service_feeds_profile_to_the_llm_callable(self):
        # Both the Studio button and POST /api/plans/<id>/draft-next-sprint
        # call draft_next_sprint_plan, so asserting the callable receives a
        # profile-bearing input proves both surfaces inherit the change.
        member = self._profiled_member('shared@test.com')
        dest = _make_plan(member, self.s_jun)

        with patch(
            'plans.services.next_sprint_draft_service.llm.is_enabled',
            return_value=True,
        ), patch(
            'plans.services.next_sprint_draft_service.draft_next_sprint',
            return_value=_draft_result(),
        ) as mock_draft:
            draft_next_sprint_plan(destination_plan=dest, actor=self.staff)

        passed_input = mock_draft.call_args.args[0]
        self.assertEqual(passed_input.persona, 'Sam — Technical Professional')
        answers = {a.prompt: a.answer for a in passed_input.onboarding_answers}
        self.assertEqual(
            answers['What are your goals?'],
            'Switch into an AI engineering role',
        )
        self.assertEqual(
            passed_input.recent_activity[0].label,
            'Opened lesson: Agents basics',
        )
