"""Studio move-unfinished-items flow (#1042)."""

import datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.utils import timezone

from plans.models import Checkpoint, Deliverable, NextStep, Plan, Sprint, Week

User = get_user_model()


def _make_plan(member, sprint, weeks=None):
    plan = Plan.objects.create(member=member, sprint=sprint)
    week_count = weeks if weeks is not None else sprint.duration_weeks
    for n in range(1, week_count + 1):
        Week.objects.create(plan=plan, week_number=n, position=n - 1)
    return plan


@tag('core')
class StudioPlanMoveUnfinishedTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.member = User.objects.create_user(email='member@test.com', password='pw')
        cls.may = Sprint.objects.create(
            name='May Sprint', slug='may-2026',
            start_date=datetime.date(2026, 5, 1), duration_weeks=6,
        )
        cls.june = Sprint.objects.create(
            name='June Sprint', slug='june-2026',
            start_date=datetime.date(2026, 6, 1), duration_weeks=6,
        )
        cls.july = Sprint.objects.create(
            name='July Sprint', slug='july-2026',
            start_date=datetime.date(2026, 7, 1), duration_weeks=4,
        )
        cls.cancelled = Sprint.objects.create(
            name='Cancelled Sprint', slug='cancelled',
            start_date=datetime.date(2026, 8, 1), duration_weeks=4,
            status='cancelled',
        )

    def setUp(self):
        self.source = _make_plan(self.member, self.may, 6)
        self.source_weeks = {
            week.week_number: week for week in self.source.weeks.all()
        }

    def _detail_url(self, plan=None):
        return f'/studio/plans/{(plan or self.source).pk}/'

    def _move_url(self, plan=None):
        return f'/studio/plans/{(plan or self.source).pk}/move-unfinished/'

    def test_detail_shows_action_only_with_unfinished_and_eligible_target(self):
        self.client.force_login(self.staff)
        response = self.client.get(self._detail_url())
        self.assertNotContains(response, 'data-testid="studio-plan-move-unfinished"')
        self.assertNotContains(response, 'No later sprint available')

        Checkpoint.objects.create(
            week=self.source_weeks[1], description='move me', position=0,
        )
        response = self.client.get(self._detail_url())
        self.assertContains(response, 'Move unfinished items to another sprint')
        self.assertContains(response, 'data-testid="studio-plan-move-unfinished"')

    def test_detail_shows_compact_unavailable_state_when_no_later_sprint(self):
        latest_sprint = Sprint.objects.create(
            name='Latest', slug='latest',
            start_date=datetime.date(2026, 9, 1),
        )
        latest_plan = _make_plan(self.member, latest_sprint, 4)
        Checkpoint.objects.create(
            week=latest_plan.weeks.get(week_number=1),
            description='move me',
            position=0,
        )

        self.client.force_login(self.staff)
        response = self.client.get(self._detail_url(latest_plan))

        self.assertContains(response, 'No later sprint available')
        self.assertNotContains(response, 'data-testid="studio-plan-move-unfinished"')

    def test_confirmation_lists_targets_in_order_and_defaults_to_next_sprint(self):
        Checkpoint.objects.create(
            week=self.source_weeks[1], description='cp one', position=0,
        )
        Checkpoint.objects.create(
            week=self.source_weeks[2], description='cp done', position=0,
            done_at=timezone.now(),
        )
        Deliverable.objects.create(plan=self.source, description='ship', position=0)
        NextStep.objects.create(plan=self.source, description='follow', position=0)

        self.client.force_login(self.staff)
        response = self.client.get(self._move_url())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'member@test.com')
        self.assertContains(response, 'May Sprint')
        self.assertContains(response, 'June Sprint')
        self.assertContains(response, 'data-testid="move-unfinished-checkpoints">1')
        self.assertContains(response, 'data-testid="move-unfinished-deliverables">1')
        self.assertContains(response, 'data-testid="move-unfinished-next-steps">1')
        self.assertContains(response, 'data-testid="move-unfinished-total">3')
        target_sprints = list(response.context['target_sprints'])
        self.assertEqual([s.slug for s in target_sprints], ['june-2026', 'july-2026'])
        self.assertEqual(response.context['selected_target'].slug, 'june-2026')

    def test_post_moves_to_selected_target_and_flashes_target_link(self):
        Checkpoint.objects.create(
            week=self.source_weeks[1], description='cp', position=0,
        )
        Checkpoint.objects.create(
            week=self.source_weeks[1], description='done cp', position=1,
            done_at=timezone.now(),
        )
        Deliverable.objects.create(plan=self.source, description='ship', position=0)

        self.client.force_login(self.staff)
        response = self.client.post(
            self._move_url(),
            {'target_sprint_slug': 'july-2026'},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.redirect_chain[-1][0], self._detail_url())
        target = Plan.objects.get(member=self.member, sprint=self.july)
        self.assertContains(response, f'/studio/plans/{target.pk}/')
        self.assertContains(response, 'Moved 2 unfinished items to "July Sprint"')
        self.assertEqual(
            [cp.description for cp in Checkpoint.objects.filter(week__plan=self.source)],
            ['done cp'],
        )
        self.assertEqual(
            [cp.description for cp in Checkpoint.objects.filter(week__plan=target)],
            ['cp'],
        )
        self.assertEqual(target.deliverables.get().description, 'ship')
        self.assertFalse(
            Plan.objects.filter(member=self.member, sprint=self.june).exists(),
        )

    @patch('studio.views.plans.move_unfinished_items_to_sprint')
    def test_post_invalid_target_rerenders_without_mutation(self, move_mock):
        Checkpoint.objects.create(
            week=self.source_weeks[1], description='stay cp', position=0,
        )
        Deliverable.objects.create(
            plan=self.source, description='stay deliverable', position=0,
        )

        self.client.force_login(self.staff)
        response = self.client.post(
            self._move_url(),
            {'target_sprint_slug': 'cancelled'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Pick a valid later sprint.')
        move_mock.assert_not_called()
        self.assertEqual(
            [cp.description for cp in Checkpoint.objects.filter(week__plan=self.source)],
            ['stay cp'],
        )
        self.assertEqual(
            [d.description for d in self.source.deliverables.all()],
            ['stay deliverable'],
        )
        self.assertFalse(
            Plan.objects.filter(member=self.member, sprint=self.june).exists(),
        )
        self.assertFalse(
            Plan.objects.filter(member=self.member, sprint=self.cancelled).exists(),
        )

    @patch('studio.views.plans.move_unfinished_items_to_sprint')
    def test_post_missing_target_rerenders_without_mutation(self, move_mock):
        NextStep.objects.create(
            plan=self.source, description='stay next step', position=0,
        )

        self.client.force_login(self.staff)
        response = self.client.post(self._move_url(), {})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Pick a valid later sprint.')
        move_mock.assert_not_called()
        self.assertEqual(
            [step.description for step in self.source.next_steps.all()],
            ['stay next step'],
        )
        self.assertFalse(
            Plan.objects.filter(member=self.member, sprint=self.june).exists(),
        )
        self.assertFalse(
            Plan.objects.filter(member=self.member, sprint=self.july).exists(),
        )

    def test_cancel_link_does_not_mutate(self):
        Checkpoint.objects.create(
            week=self.source_weeks[1], description='stay', position=0,
        )
        self.client.force_login(self.staff)

        response = self.client.get(self._move_url())

        self.assertContains(response, 'data-testid="move-unfinished-cancel"')
        self.assertEqual(
            Checkpoint.objects.filter(week__plan=self.source).count(), 1,
        )
        self.assertFalse(
            Plan.objects.filter(member=self.member, sprint=self.june).exists(),
        )

    def test_non_staff_blocked_and_no_mutation(self):
        Checkpoint.objects.create(
            week=self.source_weeks[1], description='stay', position=0,
        )
        self.client.force_login(self.member)

        get_response = self.client.get(self._move_url())
        post_response = self.client.post(
            self._move_url(),
            {'target_sprint_slug': 'june-2026'},
        )

        self.assertNotEqual(get_response.status_code, 200)
        self.assertNotEqual(post_response.status_code, 200)
        self.assertEqual(
            Checkpoint.objects.filter(week__plan=self.source).count(), 1,
        )
        self.assertFalse(
            Plan.objects.filter(member=self.member, sprint=self.june).exists(),
        )
