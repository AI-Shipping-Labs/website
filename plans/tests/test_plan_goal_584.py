"""Regression tests for short sprint goals and private details (#584)."""

import datetime
import json

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from plans.models import Plan, Sprint, SprintEnrollment, Week

User = get_user_model()


class PlanGoalModelTest(TestCase):
    def test_goal_field_defaults_to_empty_string(self):
        sprint = Sprint.objects.create(
            name='May 2026', slug='may-2026',
            start_date=datetime.date(2026, 5, 1),
        )
        member = User.objects.create_user(email='member@test.com', password='pw')
        plan = Plan.objects.create(member=member, sprint=sprint)

        self.assertEqual(plan.goal, '')
        field = Plan._meta.get_field('goal')
        self.assertEqual(field.max_length, 280)
        self.assertTrue(field.blank)
        self.assertEqual(field.default, '')


class PlanGoalRenderTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.sprint = Sprint.objects.create(
            name='May 2026', slug='may-2026',
            start_date=datetime.date(2026, 5, 1),
        )
        cls.owner = User.objects.create_user(
            email='owner@test.com', password='pw',
        )
        cls.teammate = User.objects.create_user(
            email='teammate@test.com', password='pw',
        )
        SprintEnrollment.objects.create(sprint=cls.sprint, user=cls.owner)
        SprintEnrollment.objects.create(sprint=cls.sprint, user=cls.teammate)
        cls.plan = Plan.objects.create(
            member=cls.owner,
            sprint=cls.sprint,
            visibility='cohort',
            goal='Ship one project',
            summary_current_situation='Current context',
            summary_goal='Long reflection...',
            summary_main_gap='Need ML chops',
        )
        Week.objects.create(plan=cls.plan, week_number=1, position=0)

    def _owner_url(self):
        return reverse(
            'my_plan_detail',
            kwargs={'sprint_slug': self.sprint.slug, 'plan_id': self.plan.pk},
        )

    def _teammate_url(self):
        return reverse(
            'member_plan_detail',
            kwargs={'sprint_slug': self.sprint.slug, 'plan_id': self.plan.pk},
        )

    def test_owner_sees_goal_before_weeks_and_private_details(self):
        self.client.force_login(self.owner)
        response = self.client.get(self._owner_url())
        body = response.content.decode()

        self.assertContains(response, 'data-testid="plan-goal"')
        self.assertContains(response, 'data-testid="plan-goal-text"')
        self.assertContains(response, 'Ship one project')
        self.assertLess(
            body.index('data-testid="plan-goal"'),
            body.index('data-testid="plan-weeks"'),
        )
        self.assertContains(response, 'data-testid="plan-summary"')
        self.assertContains(response, 'data-testid="plan-details"')
        self.assertContains(response, '<h2 class="text-lg font-semibold text-foreground">Details</h2>', html=True)
        self.assertContains(
            response,
            "Only you can see this section. Use it for personal context that doesn't need to be shared.",
        )
        self.assertContains(response, 'Goal (long-form)')
        self.assertNotContains(response, 'Plan context')

    def test_owner_empty_goal_shows_placeholder_and_edit_controls(self):
        self.plan.goal = ''
        self.plan.save(update_fields=['goal'])
        self.client.force_login(self.owner)

        response = self.client.get(self._owner_url())

        self.assertContains(response, 'data-testid="plan-goal"')
        self.assertContains(
            response,
            "Add a one-sentence goal so teammates know what you're shipping this sprint.",
        )
        self.assertContains(response, 'data-testid="plan-goal-edit"')
        self.assertContains(response, 'data-testid="plan-goal-input"')

    def test_teammate_sees_shared_goal_but_not_private_details(self):
        self.client.force_login(self.teammate)

        response = self.client.get(self._teammate_url())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="plan-goal"')
        self.assertContains(response, 'Ship one project')
        self.assertNotContains(response, 'data-testid="plan-details"')
        self.assertNotContains(response, 'data-testid="plan-summary"')
        self.assertNotContains(response, 'Need ML chops')
        self.assertNotContains(response, 'Long reflection...')

    def test_teammate_does_not_see_empty_goal_section(self):
        self.plan.goal = ''
        self.plan.save(update_fields=['goal'])
        self.client.force_login(self.teammate)

        response = self.client.get(self._teammate_url())

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-testid="plan-goal"')
        self.assertNotContains(response, 'Add a one-sentence goal')
        self.assertContains(response, 'data-testid="plan-weeks"')

    def test_private_plan_still_404s_for_teammate(self):
        self.plan.visibility = 'private'
        self.plan.save(update_fields=['visibility'])
        self.client.force_login(self.teammate)

        response = self.client.get(self._teammate_url())

        self.assertEqual(response.status_code, 404)


class UpdatePlanGoalTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.sprint = Sprint.objects.create(
            name='May 2026', slug='may-2026',
            start_date=datetime.date(2026, 5, 1),
        )
        cls.owner = User.objects.create_user(
            email='owner@test.com', password='pw',
        )
        cls.teammate = User.objects.create_user(
            email='teammate@test.com', password='pw',
        )
        cls.plan = Plan.objects.create(
            member=cls.owner,
            sprint=cls.sprint,
            visibility='cohort',
            goal='Original goal',
        )

    def _url(self):
        return reverse(
            'update_plan_goal',
            kwargs={'sprint_slug': self.sprint.slug, 'plan_id': self.plan.pk},
        )

    def test_owner_updates_goal(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            self._url(),
            data=json.dumps({'goal': 'Ship one project'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'ok': True, 'goal': 'Ship one project'})
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.goal, 'Ship one project')

    def test_non_owner_gets_403_and_goal_is_unchanged(self):
        self.client.force_login(self.teammate)
        response = self.client.post(
            self._url(),
            data=json.dumps({'goal': 'I am not the owner'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 403)
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.goal, 'Original goal')

    def test_overlong_goal_is_rejected_and_unchanged(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            self._url(),
            data=json.dumps({'goal': 'x' * 281}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['ok'], False)
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.goal, 'Original goal')
