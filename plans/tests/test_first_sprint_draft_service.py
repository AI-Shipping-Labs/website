"""Tests for first-sprint draft orchestration and apply (issue #1205)."""

import datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from plans.models import (
    Checkpoint,
    Deliverable,
    FirstSprintPlanDraft,
    InterviewNote,
    NextStep,
    Plan,
    Resource,
    Sprint,
    Week,
)
from plans.services.first_sprint_draft import (
    DraftResource,
    DraftWeek,
    FirstSprintDraftResult,
)
from plans.services.first_sprint_draft_service import (
    apply_first_sprint_draft,
    draft_first_sprint_plan,
)
from questionnaires.models import Answer, Questionnaire, Response, ResponseQuestion

User = get_user_model()


def _submitted_response(member):
    questionnaire = Questionnaire.objects.get(slug='onboarding-general')
    response = Response.objects.create(
        questionnaire=questionnaire,
        respondent=member,
        status='submitted',
    )
    rq = ResponseQuestion.objects.create(
        response=response,
        question_type='long_text',
        prompt='What do you want to ship?',
        order=0,
    )
    Answer.objects.create(
        response=response,
        question=rq,
        text_value='A portfolio app',
    )
    return response


def _make_plan(member, *, duration=2):
    sprint = Sprint.objects.create(
        name='July',
        slug='july-first',
        start_date=datetime.date(2026, 7, 1),
        duration_weeks=duration,
    )
    plan = Plan.objects.create(member=member, sprint=sprint)
    for n in range(1, duration + 1):
        Week.objects.create(plan=plan, week_number=n, position=n - 1)
    return plan


def _draft_result(goal='Ship a portfolio app'):
    return FirstSprintDraftResult(
        title='First sprint plan',
        goal=goal,
        summary_current_situation='New to shipping AI projects.',
        summary_goal='Publish a useful demo.',
        summary_main_gap='Needs a small scope.',
        summary_weekly_hours='~5 hours/week',
        summary_why_this_plan='It matches onboarding.',
        focus_main='Small end-to-end app',
        focus_supporting=['Use one dataset'],
        accountability='Post weekly progress.',
        weeks=[
            DraftWeek(week_number=1, theme='Scope', checkpoints=['Pick idea']),
            DraftWeek(week_number=2, theme='Ship', checkpoints=['Publish demo']),
        ],
        resources=[
            DraftResource(title='Guide', url='https://example.com', note='Read'),
        ],
        deliverables=['Public demo'],
        next_steps=['Choose project idea'],
        internal_notes='Check that the scope is realistic.',
        rationale='Onboarding asked for a portfolio app.',
    )


class FirstSprintDraftServiceTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com',
            password='pw',
            is_staff=True,
        )
        cls.member = User.objects.create_user(email='member@test.com', password='pw')

    def setUp(self):
        _submitted_response(self.member)
        self.plan = _make_plan(self.member)

    def test_draft_persisted_aside_and_live_plan_untouched(self):
        with patch(
            'plans.services.first_sprint_draft_service.llm.is_enabled',
            return_value=True,
        ), patch(
            'plans.services.first_sprint_draft_service.draft_first_sprint',
            return_value=_draft_result(),
        ):
            outcome = draft_first_sprint_plan(plan=self.plan, actor=self.staff)

        self.assertTrue(outcome['llm_enabled'])
        draft = FirstSprintPlanDraft.objects.get(plan=self.plan)
        self.assertEqual(draft.result_json['goal'], 'Ship a portfolio app')
        self.assertEqual(draft.source_response.respondent, self.member)
        self.assertEqual(draft.generated_by, self.staff)
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.goal, '')
        self.assertIsNone(self.plan.shared_at)

    def test_regenerate_overwrites_single_row(self):
        with patch(
            'plans.services.first_sprint_draft_service.llm.is_enabled',
            return_value=True,
        ), patch(
            'plans.services.first_sprint_draft_service.draft_first_sprint',
            return_value=_draft_result(goal='First'),
        ):
            draft_first_sprint_plan(plan=self.plan, actor=self.staff)
        with patch(
            'plans.services.first_sprint_draft_service.llm.is_enabled',
            return_value=True,
        ), patch(
            'plans.services.first_sprint_draft_service.draft_first_sprint',
            return_value=_draft_result(goal='Second'),
        ):
            draft_first_sprint_plan(plan=self.plan, actor=self.staff)

        drafts = FirstSprintPlanDraft.objects.filter(plan=self.plan)
        self.assertEqual(drafts.count(), 1)
        self.assertEqual(drafts.get().result_json['goal'], 'Second')

    def test_llm_off_writes_no_draft(self):
        with patch(
            'plans.services.first_sprint_draft_service.llm.is_enabled',
            return_value=False,
        ):
            outcome = draft_first_sprint_plan(plan=self.plan, actor=self.staff)

        self.assertFalse(outcome['llm_enabled'])
        self.assertIsNone(outcome['draft'])
        self.assertEqual(FirstSprintPlanDraft.objects.filter(plan=self.plan).count(), 0)

    def test_apply_writes_live_rows_deletes_draft_and_does_not_share(self):
        draft = FirstSprintPlanDraft.objects.create(
            plan=self.plan,
            source_response=Response.objects.get(respondent=self.member),
            result_json=_draft_result().model_dump(),
            generated_by=self.staff,
            generated_at=datetime.datetime(
                2026, 7, 1, 12, tzinfo=datetime.UTC,
            ),
        )

        apply_first_sprint_draft(draft=draft, actor=self.staff)

        self.plan.refresh_from_db()
        self.assertEqual(self.plan.title, 'First sprint plan')
        self.assertEqual(self.plan.goal, 'Ship a portfolio app')
        self.assertIsNone(self.plan.shared_at)
        self.assertEqual(FirstSprintPlanDraft.objects.filter(plan=self.plan).count(), 0)
        self.assertEqual(
            list(Checkpoint.objects.filter(week__plan=self.plan).values_list(
                'description', flat=True,
            )),
            ['Pick idea', 'Publish demo'],
        )
        self.assertEqual(Resource.objects.get(plan=self.plan).title, 'Guide')
        self.assertEqual(Deliverable.objects.get(plan=self.plan).description, 'Public demo')
        next_step = NextStep.objects.get(plan=self.plan)
        self.assertEqual(next_step.kind, 'pre_sprint')
        self.assertEqual(next_step.description, 'Choose project idea')
        note = InterviewNote.objects.get(plan=self.plan, visibility='internal')
        self.assertIn('realistic', note.body)
