"""Tests for ``POST /api/plans/<id>/draft-next-sprint`` (issue #891).

Covers the documented JSON shape, the LLM-off 200-with-null-draft path,
a missing plan id 4xx, and staff/token gating. The LLM is stubbed at the
service boundary.
"""

import datetime
import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import Token
from plans.models import Checkpoint, NextSprintPlanDraft, Plan, Sprint, Week
from plans.services.next_sprint_draft import NextSprintDraftResult

User = get_user_model()


def _make_plan(member, sprint, weeks=4):
    plan = Plan.objects.create(member=member, sprint=sprint)
    for n in range(1, weeks + 1):
        Week.objects.create(plan=plan, week_number=n, position=n - 1)
    return plan


def _draft_result():
    return NextSprintDraftResult(
        summary_current_situation='Now',
        summary_goal='Goal',
        summary_main_gap='Gap',
        summary_weekly_hours='~6h',
        goal='Ship it',
        suggested_next_steps=['Step'],
        rationale='Because.',
    )


class PlanDraftNextSprintApiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.member = User.objects.create_user(email='m@test.com', password='pw')
        cls.staff_token = Token.objects.create(user=cls.staff, name='s')
        cls.s_may = Sprint.objects.create(
            name='May', slug='may',
            start_date=datetime.date(2026, 5, 1), duration_weeks=4,
        )
        cls.s_jun = Sprint.objects.create(
            name='Jun', slug='jun',
            start_date=datetime.date(2026, 6, 1), duration_weeks=4,
        )

    def _auth(self, token=None):
        token = token or self.staff_token
        return {'HTTP_AUTHORIZATION': f'Token {token.key}'}

    def _url(self, plan):
        return f'/api/plans/{plan.pk}/draft-next-sprint'

    def test_returns_documented_shape_with_draft(self):
        source = _make_plan(self.member, self.s_may)
        Checkpoint.objects.create(
            week=source.weeks.get(week_number=1), description='Cp', position=0,
        )
        dest = _make_plan(self.member, self.s_jun)

        with patch(
            'plans.services.next_sprint_draft_service.llm.is_enabled',
            return_value=True,
        ), patch(
            'plans.services.next_sprint_draft_service.draft_next_sprint',
            return_value=_draft_result(),
        ):
            resp = self.client.post(self._url(dest), **self._auth())

        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertEqual(data['carried_over'], 1)
        self.assertTrue(data['llm_enabled'])
        self.assertEqual(data['source_plan_id'], source.pk)
        self.assertEqual(data['draft']['goal'], 'Ship it')
        self.assertEqual(data['draft']['suggested_next_steps'], ['Step'])

    def test_llm_off_returns_200_with_null_draft(self):
        source = _make_plan(self.member, self.s_may)
        Checkpoint.objects.create(
            week=source.weeks.get(week_number=1), description='Cp', position=0,
        )
        dest = _make_plan(self.member, self.s_jun)

        with patch(
            'plans.services.next_sprint_draft_service.llm.is_enabled',
            return_value=False,
        ):
            resp = self.client.post(self._url(dest), **self._auth())

        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertIsNone(data['draft'])
        self.assertFalse(data['llm_enabled'])
        self.assertEqual(data['carried_over'], 1)
        self.assertEqual(NextSprintPlanDraft.objects.filter(plan=dest).count(), 0)

    def test_missing_plan_is_4xx(self):
        resp = self.client.post('/api/plans/999999/draft-next-sprint', **self._auth())
        self.assertEqual(resp.status_code, 404)

    def test_missing_token_rejected_and_no_draft(self):
        dest = _make_plan(self.member, self.s_jun)
        resp = self.client.post(self._url(dest))
        self.assertIn(resp.status_code, (401, 403))
        self.assertEqual(NextSprintPlanDraft.objects.filter(plan=dest).count(), 0)

    def test_invalid_token_rejected(self):
        dest = _make_plan(self.member, self.s_jun)
        resp = self.client.post(
            self._url(dest), HTTP_AUTHORIZATION='Token not-a-real-token',
        )
        self.assertIn(resp.status_code, (401, 403))
        self.assertEqual(NextSprintPlanDraft.objects.filter(plan=dest).count(), 0)
