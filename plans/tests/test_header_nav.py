"""Tests for the "Plan" link in the public site header (issue #440)."""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from plans.models import Plan, Sprint

User = get_user_model()


class HeaderPlanLinkTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.sprint = Sprint.objects.create(
            name='May 2026', slug='may-2026',
            start_date=datetime.date(2026, 5, 1),
        )

    def setUp(self):
        self.user = User.objects.create_user(
            email='member@test.com', password='pw',
        )

    def test_authenticated_user_with_plan_sees_plan_link(self):
        plan = Plan.objects.create(
            member=self.user, sprint=self.sprint, visibility='private',
        )
        self.client.force_login(self.user)
        response = self.client.get('/')
        expected_href = reverse(
            'my_plan_detail',
            kwargs={'sprint_slug': self.sprint.slug, 'plan_id': plan.pk},
        )
        self.assertContains(response, f'href="{expected_href}"')
        self.assertContains(response, 'data-testid="header-plan-link"')
        self.assertContains(response, 'data-testid="mobile-header-plan-link"')

    def test_authenticated_user_without_plan_does_not_see_plan_link(self):
        self.client.force_login(self.user)
        response = self.client.get('/')
        self.assertNotContains(response, 'data-testid="header-plan-link"')
        self.assertNotContains(response, 'data-testid="mobile-header-plan-link"')

    def test_plan_link_points_to_most_recent_plan(self):
        older_sprint = Sprint.objects.create(
            name='April 2026', slug='april-2026',
            start_date=datetime.date(2026, 4, 1),
        )
        older_plan = Plan.objects.create(
            member=self.user, sprint=older_sprint, visibility='private',
        )
        Plan.objects.filter(pk=older_plan.pk).update(
            created_at=timezone.now() - datetime.timedelta(days=30),
        )
        newer_plan = Plan.objects.create(
            member=self.user, sprint=self.sprint, visibility='cohort',
        )

        self.client.force_login(self.user)
        response = self.client.get('/')
        expected_href = reverse(
            'my_plan_detail',
            kwargs={'sprint_slug': self.sprint.slug, 'plan_id': newer_plan.pk},
        )
        older_href = reverse(
            'my_plan_detail',
            kwargs={'sprint_slug': older_sprint.slug, 'plan_id': older_plan.pk},
        )
        self.assertContains(response, f'href="{expected_href}"')
        self.assertNotContains(
            response,
            f'data-testid="header-plan-link" href="{older_href}"',
        )

    def test_my_plan_detail_renders_view_cohort_board_cta(self):
        plan = Plan.objects.create(
            member=self.user, sprint=self.sprint, visibility='cohort',
        )
        self.client.force_login(self.user)
        url = reverse(
            'my_plan_detail',
            kwargs={'sprint_slug': self.sprint.slug, 'plan_id': plan.pk},
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        cohort_url = reverse(
            'cohort_board', kwargs={'sprint_slug': self.sprint.slug},
        )
        self.assertContains(response, f'href="{cohort_url}"')
        self.assertContains(response, 'data-testid="view-cohort-board-cta"')
        edit_url = reverse(
            'my_plan_edit',
            kwargs={'sprint_slug': self.sprint.slug, 'plan_id': plan.pk},
        )
        self.assertContains(response, f'href="{edit_url}"')
        self.assertContains(response, 'data-testid="my-plan-edit-cta"')
