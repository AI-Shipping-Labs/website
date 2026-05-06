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

from plans.models import Plan, Sprint
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
