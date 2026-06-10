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

User = get_user_model()


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
