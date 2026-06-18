"""Tests for ``POST /api/plans/<id>/move-unfinished`` (#1042)."""

import datetime
import json

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import Token
from plans.models import (
    Checkpoint,
    Deliverable,
    NextStep,
    Plan,
    Sprint,
    SprintEnrollment,
    Week,
)

User = get_user_model()


def _make_plan(member, sprint, weeks=None):
    plan = Plan.objects.create(member=member, sprint=sprint)
    week_count = weeks if weeks is not None else sprint.duration_weeks
    for n in range(1, week_count + 1):
        Week.objects.create(plan=plan, week_number=n, position=n - 1)
    return plan


class PlanMoveUnfinishedApiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.member = User.objects.create_user(email='member@test.com', password='pw')
        cls.other = User.objects.create_user(email='other@test.com', password='pw')
        cls.staff_token = Token.objects.create(user=cls.staff, name='staff')
        cls.nonstaff_token_user = User.objects.create_user(
            email='token-nonstaff@test.com',
            password='pw',
            is_staff=True,
        )
        cls.member_token = Token.objects.create(
            user=cls.nonstaff_token_user,
            name='member',
        )
        cls.nonstaff_token_user.is_staff = False
        cls.nonstaff_token_user.save(update_fields=['is_staff'])
        cls.may = Sprint.objects.create(
            name='May', slug='may-2026',
            start_date=datetime.date(2026, 5, 1), duration_weeks=4,
        )
        cls.june = Sprint.objects.create(
            name='June', slug='june-2026',
            start_date=datetime.date(2026, 6, 1), duration_weeks=4,
        )
        cls.july = Sprint.objects.create(
            name='July', slug='july-2026',
            start_date=datetime.date(2026, 7, 1), duration_weeks=4,
        )
        cls.cancelled = Sprint.objects.create(
            name='Cancelled', slug='cancelled',
            start_date=datetime.date(2026, 8, 1), duration_weeks=4,
            status='cancelled',
        )

    def setUp(self):
        self.source = _make_plan(self.member, self.may, 4)
        self.source_week = self.source.weeks.get(week_number=1)

    def _url(self, plan=None):
        return f'/api/plans/{(plan or self.source).pk}/move-unfinished'

    def _auth(self, token=None):
        token = token or self.staff_token
        return {'HTTP_AUTHORIZATION': f'Token {token.key}'}

    def _post(self, payload, *, token=None, plan=None):
        return self.client.post(
            self._url(plan),
            data=json.dumps(payload),
            content_type='application/json',
            **self._auth(token),
        )

    def test_staff_token_moves_unfinished_and_returns_summary(self):
        Checkpoint.objects.create(
            week=self.source_week, description='cp', position=0,
        )
        Checkpoint.objects.create(
            week=self.source_week, description='done cp', position=1,
            done_at=timezone.now(),
        )
        Deliverable.objects.create(
            plan=self.source, description='deliverable', position=0,
        )
        NextStep.objects.create(plan=self.source, description='step', position=0)

        resp = self._post({'target_sprint_slug': 'june-2026'})

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        target = Plan.objects.get(member=self.member, sprint=self.june)
        self.assertEqual(data['source_plan_id'], self.source.pk)
        self.assertEqual(data['source_sprint_slug'], 'may-2026')
        self.assertEqual(data['target_plan_id'], target.pk)
        self.assertEqual(data['target_sprint_slug'], 'june-2026')
        self.assertTrue(data['created_target_plan'])
        self.assertEqual(data['moved'], {
            'checkpoints': 1,
            'deliverables': 1,
            'next_steps': 1,
            'total': 3,
        })
        self.assertEqual(
            [cp.description for cp in Checkpoint.objects.filter(week__plan=self.source)],
            ['done cp'],
        )
        self.assertEqual(
            [cp.description for cp in Checkpoint.objects.filter(week__plan=target)],
            ['cp'],
        )
        self.assertEqual(target.deliverables.get().description, 'deliverable')
        self.assertEqual(target.next_steps.get().description, 'step')
        self.assertTrue(
            SprintEnrollment.objects.filter(
                sprint=self.june,
                user=self.member,
                enrolled_by=self.staff,
            ).exists(),
        )

    def test_reuses_existing_target_plan(self):
        target = _make_plan(self.member, self.june, 4)
        target.goal = 'Existing'
        target.save()
        Checkpoint.objects.create(
            week=target.weeks.get(week_number=1),
            description='existing',
            position=0,
        )
        Checkpoint.objects.create(
            week=self.source_week, description='moved', position=0,
        )

        resp = self._post({'target_sprint_slug': 'june-2026'})

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertFalse(data['created_target_plan'])
        target.refresh_from_db()
        self.assertEqual(target.goal, 'Existing')
        self.assertEqual(
            [
                cp.description
                for cp in target.weeks.get(week_number=1).checkpoints.all()
            ],
            ['existing', 'moved'],
        )

    def test_missing_nonstaff_and_invalid_tokens_do_not_mutate(self):
        Checkpoint.objects.create(
            week=self.source_week, description='stay', position=0,
        )
        cases = (
            ({}, None),
            ({'HTTP_AUTHORIZATION': f'Token {self.member_token.key}'}, None),
            ({'HTTP_AUTHORIZATION': 'Token not-real'}, None),
        )
        for headers, expected_status in cases:
            with self.subTest(headers=headers):
                resp = self.client.post(
                    self._url(),
                    data=json.dumps({'target_sprint_slug': 'june-2026'}),
                    content_type='application/json',
                    **headers,
                )
                if expected_status is not None:
                    self.assertEqual(resp.status_code, expected_status)
                else:
                    self.assertIn(resp.status_code, (401, 403))

        self.assertEqual(
            Checkpoint.objects.filter(week__plan=self.source).count(), 1,
        )
        self.assertFalse(
            Plan.objects.filter(member=self.member, sprint=self.june).exists(),
        )

    def test_validation_errors_are_stable_and_do_not_mutate(self):
        Checkpoint.objects.create(
            week=self.source_week, description='stay', position=0,
        )
        earlier = Sprint.objects.create(
            name='April', slug='april-2026',
            start_date=datetime.date(2026, 4, 1), duration_weeks=4,
        )
        cases = (
            ({'target_sprint_slug': 'missing'}, 404, 'unknown_target_sprint'),
            ({'target_sprint_slug': 'cancelled'}, 422, 'cancelled_target_sprint'),
            ({'target_sprint_slug': 'may-2026'}, 422, 'target_sprint_not_later'),
            ({'target_sprint_slug': 'april-2026'}, 422, 'target_sprint_not_later'),
            ({}, 400, 'missing_field'),
            ({'target_sprint_slug': ''}, 422, 'validation_error'),
        )
        for payload, status, code in cases:
            with self.subTest(payload=payload):
                resp = self._post(payload)
                self.assertEqual(resp.status_code, status)
                self.assertEqual(resp.json()['code'], code)

        self.assertEqual(
            Checkpoint.objects.filter(week__plan=self.source).count(), 1,
        )
        self.assertFalse(
            Plan.objects.filter(member=self.member, sprint=self.june).exists(),
        )
        self.assertFalse(
            Plan.objects.filter(member=self.member, sprint=self.cancelled).exists(),
        )
        self.assertFalse(
            Plan.objects.filter(member=self.member, sprint=earlier).exists(),
        )

    def test_invalid_json_and_unknown_source_are_stable_errors(self):
        resp = self.client.post(
            self._url(),
            data='{',
            content_type='application/json',
            **self._auth(),
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()['code'], 'invalid_json')

        resp = self.client.post(
            '/api/plans/999999/move-unfinished',
            data=json.dumps({'target_sprint_slug': 'june-2026'}),
            content_type='application/json',
            **self._auth(),
        )
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()['code'], 'unknown_plan')

    def test_rerun_reports_no_unfinished_and_does_not_duplicate(self):
        Checkpoint.objects.create(
            week=self.source_week, description='move once', position=0,
        )
        first = self._post({'target_sprint_slug': 'june-2026'})
        self.assertEqual(first.status_code, 200)

        second = self._post({'target_sprint_slug': 'june-2026'})

        self.assertEqual(second.status_code, 422)
        self.assertEqual(second.json()['code'], 'no_unfinished_items')
        target = Plan.objects.get(member=self.member, sprint=self.june)
        self.assertEqual(
            Checkpoint.objects.filter(week__plan=target).count(), 1,
        )
