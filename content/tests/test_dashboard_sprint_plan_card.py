"""Tests for the "Your sprint plan" card on the home dashboard (issue #442).

The authenticated home view (rendered from
``templates/content/dashboard.html``) carries the same card with the
same context keys as the Account page. These tests verify the
member-dashboard surface mirrors the Account-page surface and that
non-participant members see no sprint copy.
"""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from content.access import LEVEL_MAIN, LEVEL_PREMIUM
from plans.models import Plan, Sprint, SprintEnrollment
from tests.fixtures import TierSetupMixin

User = get_user_model()


class DashboardSprintPlanCardTest(TierSetupMixin, TestCase):
    def test_dashboard_card_shown_when_user_has_plan(self):
        user = User.objects.create_user(
            email='member@test.com', password='pw',
        )
        sprint = Sprint.objects.create(
            name='August 2026', slug='august-2026',
            start_date=datetime.date(2026, 8, 1),
            duration_weeks=8,
            status='active',
        )
        plan = Plan.objects.create(
            member=user, sprint=sprint, status='active',
        )

        self.client.login(email='member@test.com', password='pw')
        response = self.client.get('/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['plan'].pk, plan.pk)
        self.assertContains(
            response, 'data-testid="account-sprint-plan-card"',
        )
        # The Open my plan CTA points at the read-only owner view.
        expected_href = reverse('my_plan_detail', kwargs={'plan_id': plan.pk})
        self.assertContains(response, f'href="{expected_href}"')
        # Sprint metadata is rendered.
        self.assertContains(response, 'August 2026')
        self.assertContains(response, '8 weeks')

    def test_dashboard_card_hidden_when_user_has_no_plan(self):
        User.objects.create_user(
            email='nopl@test.com', password='pw',
        )

        self.client.login(email='nopl@test.com', password='pw')
        response = self.client.get('/')

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context['plan'])
        self.assertNotContains(
            response, 'data-testid="account-sprint-plan-card"',
        )

    def test_anonymous_homepage_has_no_sprint_card(self):
        response = self.client.get('/')

        # Anonymous users see the public marketing homepage; the sprint
        # card markup must not be rendered there.
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(
            response, 'data-testid="account-sprint-plan-card"',
        )

    def test_eligible_user_without_plan_sees_active_sprint_opportunity(self):
        User.objects.create_user(
            email='main-sprint@test.com', password='pw', tier=self.main_tier,
        )
        sprint = Sprint.objects.create(
            name='Main Sprint', slug='main-sprint',
            start_date=datetime.date(2026, 8, 1),
            duration_weeks=6,
            status='active',
            min_tier_level=LEVEL_MAIN,
        )

        self.client.login(email='main-sprint@test.com', password='pw')
        response = self.client.get('/')

        self.assertContains(response, 'Sprints & Cohorts')
        self.assertContains(response, 'Main Sprint')
        self.assertContains(
            response,
            reverse('sprint_detail', kwargs={'sprint_slug': sprint.slug}),
        )
        self.assertContains(response, 'View sprint')

    def test_ineligible_user_does_not_see_locked_active_sprint(self):
        User.objects.create_user(
            email='basic-sprint@test.com', password='pw', tier=self.basic_tier,
        )
        Sprint.objects.create(
            name='Premium Sprint', slug='premium-sprint',
            start_date=datetime.date(2026, 8, 1),
            duration_weeks=6,
            status='active',
            min_tier_level=LEVEL_PREMIUM,
        )

        self.client.login(email='basic-sprint@test.com', password='pw')
        response = self.client.get('/')

        self.assertNotContains(response, 'Premium Sprint')
        self.assertContains(response, 'No active sprint openings for your tier')
        self.assertContains(response, 'href="/activities"')

    def test_enrolled_user_without_plan_links_active_sprint_to_cohort(self):
        user = User.objects.create_user(
            email='enrolled-sprint@test.com', password='pw', tier=self.main_tier,
        )
        sprint = Sprint.objects.create(
            name='Enrolled Sprint', slug='enrolled-sprint',
            start_date=datetime.date(2026, 8, 1),
            duration_weeks=6,
            status='active',
            min_tier_level=LEVEL_MAIN,
        )
        SprintEnrollment.objects.create(user=user, sprint=sprint)

        self.client.login(email='enrolled-sprint@test.com', password='pw')
        response = self.client.get('/')

        self.assertContains(response, 'Enrolled Sprint')
        self.assertContains(
            response,
            reverse('cohort_board', kwargs={'sprint_slug': sprint.slug}),
        )
        self.assertContains(response, 'View cohort')

    def test_user_with_plan_keeps_plan_card_and_can_see_other_sprint(self):
        user = User.objects.create_user(
            email='planned-sprint@test.com', password='pw', tier=self.main_tier,
        )
        current = Sprint.objects.create(
            name='Current Sprint', slug='current-sprint',
            start_date=datetime.date(2026, 8, 1),
            duration_weeks=6,
            status='active',
            min_tier_level=LEVEL_MAIN,
        )
        other = Sprint.objects.create(
            name='Other Sprint', slug='other-sprint',
            start_date=datetime.date(2026, 9, 1),
            duration_weeks=6,
            status='active',
            min_tier_level=LEVEL_MAIN,
        )
        plan = Plan.objects.create(
            member=user, sprint=current, status='active',
        )

        self.client.login(email='planned-sprint@test.com', password='pw')
        response = self.client.get('/')

        self.assertContains(
            response, 'data-testid="account-sprint-plan-card"',
        )
        self.assertContains(
            response,
            reverse('my_plan_detail', kwargs={'plan_id': plan.pk}),
        )
        self.assertContains(response, 'Other Sprint')
        self.assertContains(
            response,
            reverse('sprint_detail', kwargs={'sprint_slug': other.slug}),
        )
