"""Tests for the "Your sprint plan" card on the Account page (issue #442).

The card:

- Renders only when the viewer has a :class:`plans.models.Plan` row.
- Shows sprint name, start date, duration in weeks, and the plan's
  ``get_status_display`` value.
- Shows a ``{done} of {total} checkpoints done`` line only when there
  is at least one checkpoint on the plan.
- Hides the "View cohort" link unless at least one OTHER plan in the
  same sprint has cohort visibility.
- Uses the user's most recently created plan when they have several.
- Is scoped per-user (one user's plan never bleeds into another's
  account page).
"""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from plans.models import Checkpoint, Plan, Sprint, Week

User = get_user_model()


class AccountSprintPlanCardHiddenWithoutPlanTest(TestCase):
    def test_card_absent_when_user_has_no_plan(self):
        user = User.objects.create_user(email='noplan@test.com', password='pw')
        self.client.force_login(user)

        response = self.client.get('/account/')

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context['plan'])
        self.assertNotContains(response, 'data-testid="account-sprint-plan-card"')
        # The CTAs must also be absent.
        self.assertNotContains(response, 'data-testid="account-sprint-plan-open"')
        self.assertNotContains(response, 'data-testid="account-sprint-plan-cohort"')


class AccountSprintPlanCardVisibleWithPlanTest(TestCase):
    def setUp(self):
        self.sprint = Sprint.objects.create(
            name='May 2026',
            slug='may-2026',
            start_date=datetime.date(2026, 5, 1),
            duration_weeks=6,
            status='active',
        )
        self.user = User.objects.create_user(
            email='member@test.com', password='pw',
        )
        self.plan = Plan.objects.create(
            member=self.user,
            sprint=self.sprint,
            status='active',
            visibility='private',
        )
        self.client.force_login(self.user)

    def test_card_renders_with_sprint_name_and_status(self):
        response = self.client.get('/account/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="account-sprint-plan-card"')
        self.assertContains(
            response, 'data-testid="account-sprint-plan-name"',
        )
        # The card surfaces the sprint name verbatim.
        self.assertContains(response, 'May 2026')
        # The plan's status display label is shown.
        self.assertContains(
            response, '<span data-testid="account-sprint-plan-status">Active</span>',
            html=True,
        )

    def test_card_shows_duration_and_start_date(self):
        response = self.client.get('/account/')

        self.assertContains(response, '01 May 2026')
        self.assertContains(response, '6 weeks')

    def test_card_shows_open_my_plan_with_correct_href(self):
        response = self.client.get('/account/')

        expected_href = reverse('my_plan_detail', kwargs={'plan_id': self.plan.pk})
        self.assertContains(
            response, f'href="{expected_href}"',
        )
        self.assertContains(
            response, 'data-testid="account-sprint-plan-open"',
        )

    def test_card_does_not_show_cohort_link_when_no_other_shared_plan(self):
        response = self.client.get('/account/')

        self.assertFalse(response.context['cohort_has_other_shared_plans'])
        self.assertNotContains(
            response, 'data-testid="account-sprint-plan-cohort"',
        )


class AccountSprintPlanCardCohortLinkTest(TestCase):
    def setUp(self):
        self.sprint = Sprint.objects.create(
            name='May 2026',
            slug='may-2026',
            start_date=datetime.date(2026, 5, 1),
            duration_weeks=4,
            status='active',
        )
        self.viewer = User.objects.create_user(
            email='viewer@test.com', password='pw',
        )
        self.teammate = User.objects.create_user(
            email='teammate@test.com', password='pw',
        )

    def test_card_hides_cohort_link_when_only_private_plans(self):
        Plan.objects.create(
            member=self.viewer, sprint=self.sprint, visibility='private',
        )
        Plan.objects.create(
            member=self.teammate, sprint=self.sprint, visibility='private',
        )
        self.client.force_login(self.viewer)

        response = self.client.get('/account/')

        self.assertFalse(response.context['cohort_has_other_shared_plans'])
        self.assertNotContains(
            response, 'data-testid="account-sprint-plan-cohort"',
        )

    def test_card_shows_cohort_link_when_teammate_shares(self):
        Plan.objects.create(
            member=self.viewer, sprint=self.sprint, visibility='private',
        )
        Plan.objects.create(
            member=self.teammate, sprint=self.sprint, visibility='cohort',
        )
        self.client.force_login(self.viewer)

        response = self.client.get('/account/')

        self.assertTrue(response.context['cohort_has_other_shared_plans'])
        self.assertContains(
            response, 'data-testid="account-sprint-plan-cohort"',
        )
        expected_href = reverse(
            'cohort_board', kwargs={'sprint_slug': self.sprint.slug},
        )
        self.assertContains(response, f'href="{expected_href}"')

    def test_cohort_link_ignores_viewers_own_cohort_visibility(self):
        """A solo cohort-visibility plan (no teammate) does NOT light up the link.

        The link advertises content, not just intent. If the viewer is
        the only person on cohort, clicking would lead to an empty
        board -- so we hide it.
        """
        Plan.objects.create(
            member=self.viewer, sprint=self.sprint, visibility='cohort',
        )
        Plan.objects.create(
            member=self.teammate, sprint=self.sprint, visibility='private',
        )
        self.client.force_login(self.viewer)

        response = self.client.get('/account/')

        self.assertFalse(response.context['cohort_has_other_shared_plans'])
        self.assertNotContains(
            response, 'data-testid="account-sprint-plan-cohort"',
        )


class AccountSprintPlanCardLatestPlanTest(TestCase):
    def test_card_uses_most_recently_created_plan_when_user_has_several(self):
        user = User.objects.create_user(email='multi@test.com', password='pw')
        sprint_a = Sprint.objects.create(
            name='Sprint A', slug='sprint-a',
            start_date=datetime.date(2026, 1, 1),
        )
        sprint_b = Sprint.objects.create(
            name='Sprint B', slug='sprint-b',
            start_date=datetime.date(2026, 5, 1),
        )

        older_plan = Plan.objects.create(member=user, sprint=sprint_a)
        # Force the older plan's created_at into the past so it is
        # unambiguously "older" regardless of test-runner timing.
        Plan.objects.filter(pk=older_plan.pk).update(
            created_at=timezone.now() - datetime.timedelta(days=30),
        )
        newer_plan = Plan.objects.create(member=user, sprint=sprint_b)

        self.client.force_login(user)

        response = self.client.get('/account/')

        self.assertEqual(response.context['plan'].pk, newer_plan.pk)
        self.assertContains(response, 'Sprint B')
        # The older sprint's name must NOT appear on the card.
        self.assertNotContains(response, 'Sprint A')


class AccountSprintPlanCardProgressTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='progress@test.com', password='pw')
        self.sprint = Sprint.objects.create(
            name='June 2026', slug='june-2026',
            start_date=datetime.date(2026, 6, 1),
        )
        self.plan = Plan.objects.create(member=self.user, sprint=self.sprint)
        self.client.force_login(self.user)

    def test_progress_line_hidden_when_no_checkpoints(self):
        response = self.client.get('/account/')

        self.assertEqual(response.context['plan_progress_total'], 0)
        self.assertNotContains(
            response, 'data-testid="account-sprint-plan-progress"',
        )

    def test_progress_line_shows_done_and_total_counts(self):
        week = Week.objects.create(plan=self.plan, week_number=1)
        # 5 checkpoints, 2 with done_at set.
        Checkpoint.objects.create(
            week=week, description='one', done_at=timezone.now(),
        )
        Checkpoint.objects.create(
            week=week, description='two', done_at=timezone.now(),
        )
        Checkpoint.objects.create(week=week, description='three')
        Checkpoint.objects.create(week=week, description='four')
        Checkpoint.objects.create(week=week, description='five')

        response = self.client.get('/account/')

        self.assertEqual(response.context['plan_progress_total'], 5)
        self.assertEqual(response.context['plan_progress_done'], 2)
        self.assertContains(
            response, 'data-testid="account-sprint-plan-progress"',
        )
        self.assertContains(response, '2 of 5 checkpoints done')


class AccountSprintPlanCardOwnershipTest(TestCase):
    def test_card_does_not_leak_other_users_plan(self):
        owner = User.objects.create_user(email='owner@test.com', password='pw')
        viewer = User.objects.create_user(email='viewer@test.com', password='pw')
        sprint = Sprint.objects.create(
            name='Sprint Z', slug='sprint-z',
            start_date=datetime.date(2026, 7, 1),
        )
        Plan.objects.create(member=owner, sprint=sprint)

        self.client.force_login(viewer)

        response = self.client.get('/account/')

        # Viewer has no plan of their own -> the card is hidden.
        self.assertIsNone(response.context['plan'])
        self.assertNotContains(
            response, 'data-testid="account-sprint-plan-card"',
        )
        # The owner's sprint name must not appear anywhere on the
        # viewer's account page.
        self.assertNotContains(response, 'Sprint Z')
