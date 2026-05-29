"""Studio staff carry-over trigger (issue #808).

POST /studio/plans/<id>/carry-over/ calls the same carry-over service the
member uses, with the same source selection and idempotency, and reports
the copied count via a Studio flash while staying on the plan detail page.
"""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from plans.models import Checkpoint, Plan, Sprint, Week

User = get_user_model()


def _make_plan(member, sprint, weeks):
    plan = Plan.objects.create(member=member, sprint=sprint)
    for n in range(1, weeks + 1):
        Week.objects.create(plan=plan, week_number=n, position=n - 1)
    return plan


@tag('core')
class StudioPlanCarryOverTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.member = User.objects.create_user(
            email='member@test.com', password='pw',
        )
        cls.s_prev = Sprint.objects.create(
            name='Prev Sprint', slug='prev',
            start_date=datetime.date(2026, 1, 1), duration_weeks=4,
        )
        cls.s_next = Sprint.objects.create(
            name='Next Sprint', slug='next',
            start_date=datetime.date(2026, 5, 1), duration_weeks=4,
        )

    def setUp(self):
        self.source = _make_plan(self.member, self.s_prev, 4)
        Checkpoint.objects.create(
            week=self.source.weeks.get(week_number=1),
            description='carry me', position=0,
        )
        self.dest = _make_plan(self.member, self.s_next, 4)

    def _url(self, plan):
        return f'/studio/plans/{plan.pk}/carry-over/'

    def test_non_staff_blocked(self):
        self.client.force_login(self.member)
        resp = self.client.post(self._url(self.dest))
        self.assertNotEqual(resp.status_code, 200)
        self.assertEqual(
            Checkpoint.objects.filter(week__plan=self.dest).count(), 0,
        )

    def test_staff_copies_and_reports_count(self):
        self.client.force_login(self.staff)
        resp = self.client.post(self._url(self.dest), follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.redirect_chain[-1][0], f'/studio/plans/{self.dest.pk}/')
        self.assertEqual(
            Checkpoint.objects.filter(week__plan=self.dest).count(), 1,
        )
        msgs = [m.message for m in resp.context['messages']]
        self.assertTrue(any('Carried over 1 task' in m for m in msgs))
        self.assertTrue(any('Prev Sprint' in m for m in msgs))

    def test_rerun_reports_no_op(self):
        self.client.force_login(self.staff)
        self.client.post(self._url(self.dest))
        resp = self.client.post(self._url(self.dest), follow=True)
        self.assertEqual(
            Checkpoint.objects.filter(week__plan=self.dest).count(), 1,
        )
        msgs = [m.message for m in resp.context['messages']]
        self.assertTrue(any('No new tasks' in m for m in msgs))

    def test_no_prior_plan_reports_info(self):
        # A member whose only plan is in the latest sprint -> no source.
        lone = User.objects.create_user(email='lone@test.com', password='pw')
        lone_plan = _make_plan(lone, self.s_next, 4)
        self.client.force_login(self.staff)
        resp = self.client.post(self._url(lone_plan), follow=True)
        msgs = [m.message for m in resp.context['messages']]
        self.assertTrue(any('no previous sprint plan' in m for m in msgs))
        # source/only_plan untouched
        self.assertEqual(
            Checkpoint.objects.filter(week__plan=lone_plan).count(), 0,
        )
