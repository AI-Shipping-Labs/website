"""View tests for the member carry-over action and panel (issue #808).

Covers the owner-only ``POST /sprints/<slug>/plan/<id>/carry-over`` route
(404 for non-owner / wrong sprint, login redirect for anonymous, success
copy + flash) and the carry-over panel rendering on ``my_plan_detail``.
"""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from plans.models import Checkpoint, Plan, Sprint, Week

User = get_user_model()


def _make_plan(member, sprint, weeks):
    plan = Plan.objects.create(member=member, sprint=sprint)
    for n in range(1, weeks + 1):
        Week.objects.create(plan=plan, week_number=n, position=n - 1)
    return plan


class CarryOverRouteAccessTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(email='m@test.com', password='pw')
        cls.other = User.objects.create_user(email='o@test.com', password='pw')
        cls.s_prev = Sprint.objects.create(
            name='Prev', slug='prev',
            start_date=datetime.date(2026, 1, 1), duration_weeks=4,
        )
        cls.s_next = Sprint.objects.create(
            name='Next', slug='next',
            start_date=datetime.date(2026, 5, 1), duration_weeks=4,
        )
        cls.source = _make_plan(cls.member, cls.s_prev, 4)
        Checkpoint.objects.create(
            week=cls.source.weeks.get(week_number=1),
            description='carry me', position=0,
        )
        cls.dest = _make_plan(cls.member, cls.s_next, 4)

    def _url(self, plan):
        return reverse(
            'carry_over_tasks',
            kwargs={'sprint_slug': plan.sprint.slug, 'plan_id': plan.pk},
        )

    def test_anonymous_redirected_to_login_no_copy(self):
        resp = self.client.post(self._url(self.dest))
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/login', resp.url)
        self.assertEqual(
            Checkpoint.objects.filter(week__plan=self.dest).count(), 0,
        )

    def test_non_owner_gets_404_no_copy(self):
        self.client.force_login(self.other)
        resp = self.client.post(self._url(self.dest))
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(
            Checkpoint.objects.filter(week__plan=self.dest).count(), 0,
        )

    def test_wrong_sprint_slug_gets_404(self):
        self.client.force_login(self.member)
        bad = reverse(
            'carry_over_tasks',
            kwargs={'sprint_slug': self.s_prev.slug, 'plan_id': self.dest.pk},
        )
        resp = self.client.post(bad)
        self.assertEqual(resp.status_code, 404)

    def test_get_not_allowed(self):
        self.client.force_login(self.member)
        resp = self.client.get(self._url(self.dest))
        self.assertEqual(resp.status_code, 405)

    def test_owner_copies_and_redirects_with_success_message(self):
        self.client.force_login(self.member)
        resp = self.client.post(self._url(self.dest), follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            Checkpoint.objects.filter(week__plan=self.dest).count(), 1,
        )
        msgs = [m.message for m in resp.context['messages']]
        self.assertTrue(any('Carried over 1 task' in m for m in msgs))
        self.assertTrue(any('Prev' in m for m in msgs))

    def test_owner_compacts_later_source_week_to_week_one(self):
        Checkpoint.objects.filter(week__plan=self.source).delete()
        Checkpoint.objects.create(
            week=self.source.weeks.get(week_number=3),
            description='late unfinished', position=0,
        )
        self.client.force_login(self.member)
        resp = self.client.post(self._url(self.dest), follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            [
                c.description
                for c in self.dest.weeks.get(week_number=1).checkpoints.all()
            ],
            ['late unfinished'],
        )
        self.assertEqual(
            self.dest.weeks.get(week_number=3).checkpoints.count(),
            0,
        )

    def test_rerun_is_no_op_with_info_message(self):
        self.client.force_login(self.member)
        self.client.post(self._url(self.dest))
        resp = self.client.post(self._url(self.dest), follow=True)
        self.assertEqual(
            Checkpoint.objects.filter(week__plan=self.dest).count(), 1,
        )
        msgs = [m.message for m in resp.context['messages']]
        self.assertTrue(any('caught up' in m.lower() for m in msgs))


class CarryOverPanelRenderTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(email='m@test.com', password='pw')
        cls.other = User.objects.create_user(email='o@test.com', password='pw')
        cls.s_prev = Sprint.objects.create(
            name='Spring Sprint', slug='spring',
            start_date=datetime.date(2026, 1, 1), duration_weeks=4,
        )
        cls.s_next = Sprint.objects.create(
            name='Summer Sprint', slug='summer',
            start_date=datetime.date(2026, 5, 1), duration_weeks=4,
        )

    def _my_plan_url(self, plan):
        return reverse(
            'my_plan_detail',
            kwargs={'sprint_slug': plan.sprint.slug, 'plan_id': plan.pk},
        )

    def test_panel_shown_with_source_name_and_count(self):
        source = _make_plan(self.member, self.s_prev, 4)
        Checkpoint.objects.create(
            week=source.weeks.get(week_number=1), description='a', position=0,
        )
        Checkpoint.objects.create(
            week=source.weeks.get(week_number=2), description='b', position=0,
        )
        dest = _make_plan(self.member, self.s_next, 4)
        self.client.force_login(self.member)
        resp = self.client.get(self._my_plan_url(dest))
        self.assertContains(resp, 'data-testid="plan-carry-over-panel"')
        self.assertContains(resp, 'Spring Sprint')
        self.assertContains(resp, '2 unfinished tasks available')
        self.assertContains(resp, 'Carry over 2 tasks')

    def test_panel_hidden_when_no_prior_plan(self):
        dest = _make_plan(self.member, self.s_next, 4)
        self.client.force_login(self.member)
        resp = self.client.get(self._my_plan_url(dest))
        self.assertNotContains(resp, 'data-testid="plan-carry-over-panel"')

    def test_panel_hidden_when_all_source_items_finished(self):
        source = _make_plan(self.member, self.s_prev, 4)
        Checkpoint.objects.create(
            week=source.weeks.get(week_number=1), description='done',
            position=0, done_at=timezone.now(),
        )
        dest = _make_plan(self.member, self.s_next, 4)
        self.client.force_login(self.member)
        resp = self.client.get(self._my_plan_url(dest))
        self.assertNotContains(resp, 'data-testid="plan-carry-over-panel"')

    def test_panel_shows_caught_up_after_carry_over(self):
        source = _make_plan(self.member, self.s_prev, 4)
        Checkpoint.objects.create(
            week=source.weeks.get(week_number=1), description='a', position=0,
        )
        dest = _make_plan(self.member, self.s_next, 4)
        self.client.force_login(self.member)
        self.client.post(reverse(
            'carry_over_tasks',
            kwargs={'sprint_slug': dest.sprint.slug, 'plan_id': dest.pk},
        ))
        resp = self.client.get(self._my_plan_url(dest))
        self.assertContains(resp, 'data-testid="plan-carry-over-caught-up"')
        self.assertNotContains(resp, 'data-testid="plan-carry-over-submit"')

    def test_panel_not_on_read_only_teammate_view(self):
        # Both members enrolled in the same sprint; the source plan exists
        # for the owner. A teammate viewing the cohort plan must never see
        # the carry-over panel.
        source = _make_plan(self.member, self.s_prev, 4)
        Checkpoint.objects.create(
            week=source.weeks.get(week_number=1), description='a', position=0,
        )
        dest = _make_plan(self.member, self.s_next, 4)
        dest.visibility = 'cohort'
        dest.save(update_fields=['visibility'])
        # The teammate must be enrolled in the same sprint to satisfy the
        # cohort-visibility read predicate. Creating their plan in s_next
        # back-creates the enrollment via the Plan post_save signal.
        _make_plan(self.other, self.s_next, 4)
        self.client.force_login(self.other)
        url = reverse(
            'member_plan_detail',
            kwargs={'sprint_slug': dest.sprint.slug, 'plan_id': dest.pk},
        )
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'data-testid="plan-carry-over-panel"')
