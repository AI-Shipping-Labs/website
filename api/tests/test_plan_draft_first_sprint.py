"""Tests for first-sprint draft API endpoints (issue #1205)."""

import datetime
import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import Token
from plans.models import FirstSprintPlanDraft, Plan, Sprint, Week
from plans.services.first_sprint_draft import (
    DraftWeek,
    FirstSprintDraftResult,
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
        prompt='Goal?',
        order=0,
    )
    Answer.objects.create(response=response, question=rq, text_value='Ship app')
    return response


def _make_plan(member):
    sprint = Sprint.objects.create(
        name='July',
        slug='july-api-first',
        start_date=datetime.date(2026, 7, 1),
        duration_weeks=2,
    )
    plan = Plan.objects.create(member=member, sprint=sprint)
    for n in range(1, 3):
        Week.objects.create(plan=plan, week_number=n, position=n - 1)
    return plan


def _draft_result():
    return FirstSprintDraftResult(
        title='First sprint',
        goal='Ship app',
        weeks=[
            DraftWeek(week_number=1, theme='Scope', checkpoints=['Pick']),
            DraftWeek(week_number=2, theme='Ship', checkpoints=['Publish']),
        ],
        deliverables=['Demo'],
        next_steps=['Confirm scope'],
        rationale='Onboarding.',
    )


class FirstSprintDraftApiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com',
            password='pw',
            is_staff=True,
        )
        cls.member = User.objects.create_user(email='member@test.com', password='pw')
        cls.staff_token = Token.objects.create(user=cls.staff, name='s')

    def setUp(self):
        _submitted_response(self.member)
        self.plan = _make_plan(self.member)

    def _auth(self):
        return {'HTTP_AUTHORIZATION': f'Token {self.staff_token.key}'}

    def test_draft_endpoint_creates_held_aside_draft(self):
        with patch(
            'plans.services.first_sprint_draft_service.llm.is_enabled',
            return_value=True,
        ), patch(
            'plans.services.first_sprint_draft_service.draft_first_sprint',
            return_value=_draft_result(),
        ):
            response = self.client.post(
                f'/api/plans/{self.plan.pk}/draft-first-sprint',
                **self._auth(),
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body['llm_enabled'])
        self.assertFalse(body['draft_error'])
        self.assertEqual(body['draft']['result']['goal'], 'Ship app')
        self.assertEqual(FirstSprintPlanDraft.objects.filter(plan=self.plan).count(), 1)
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.goal, '')

    def test_apply_endpoint_writes_nested_plan_detail_without_sharing(self):
        FirstSprintPlanDraft.objects.create(
            plan=self.plan,
            source_response=Response.objects.get(respondent=self.member),
            result_json=_draft_result().model_dump(),
            generated_by=self.staff,
            generated_at=datetime.datetime(
                2026, 7, 1, 12, tzinfo=datetime.UTC,
            ),
        )

        response = self.client.post(
            f'/api/plans/{self.plan.pk}/draft-first-sprint/apply',
            **self._auth(),
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['goal'], 'Ship app')
        self.assertIsNone(body['shared_at'])
        self.assertEqual(body['weeks'][0]['checkpoints'][0]['description'], 'Pick')
        self.assertEqual(FirstSprintPlanDraft.objects.filter(plan=self.plan).count(), 0)

    def test_patch_visibility_accepts_private_and_cohort_rejects_public(self):
        response = self.client.patch(
            f'/api/plans/{self.plan.pk}',
            data=json.dumps({'visibility': 'cohort'}),
            content_type='application/json',
            **self._auth(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['visibility'], 'cohort')
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.visibility, 'cohort')

        response = self.client.patch(
            f'/api/plans/{self.plan.pk}',
            data=json.dumps({'visibility': 'public'}),
            content_type='application/json',
            **self._auth(),
        )
        self.assertEqual(response.status_code, 422)
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.visibility, 'cohort')
