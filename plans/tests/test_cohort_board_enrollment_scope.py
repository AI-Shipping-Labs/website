"""Cohort board scopes via SprintEnrollment, not plan-existence (issue #443).

The #440 tests still pass via the post_save signal back-creating the
enrollment when ``Plan.objects.create()`` is called. These tests cover
the new edge cases where membership is decoupled from plan existence:

- Enrolled-without-plan -> board renders 200 with a "plan is being
  prepared" placeholder.
- Plan-without-enrollment -> board returns 404 (the "ghost plan" case
  after a staff DELETE).
- Plans whose owner has no enrollment do NOT appear in the visible-
  on-cohort-board queryset.
"""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from plans.models import Plan, Sprint, SprintEnrollment

User = get_user_model()


class EnrolledWithoutPlanTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.sprint = Sprint.objects.create(
            name='May 2026', slug='may-2026',
            start_date=datetime.date(2026, 5, 1),
        )
        cls.member = User.objects.create_user(
            email='m@test.com', password='pw',
        )
        SprintEnrollment.objects.create(sprint=cls.sprint, user=cls.member)

    def test_board_renders_200_for_enrolled_without_plan(self):
        self.client.force_login(self.member)
        url = reverse('cohort_board', kwargs={'sprint_slug': self.sprint.slug})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context['viewer_plan'])
        # Placeholder block is shown.
        self.assertContains(response, 'data-testid="viewer-plan-pending"')


class PlanWithoutEnrollmentTest(TestCase):
    """Ghost plan case: plan exists but enrollment was purged."""

    @classmethod
    def setUpTestData(cls):
        cls.sprint = Sprint.objects.create(
            name='May 2026', slug='may-2026',
            start_date=datetime.date(2026, 5, 1),
        )
        cls.ghost = User.objects.create_user(
            email='ghost@test.com', password='pw',
        )
        Plan.objects.create(member=cls.ghost, sprint=cls.sprint, visibility='cohort')
        # Simulate "staff purged the enrollment via DELETE API".
        SprintEnrollment.objects.filter(
            sprint=cls.sprint, user=cls.ghost,
        ).delete()

    def test_ghost_user_gets_404(self):
        self.client.force_login(self.ghost)
        url = reverse('cohort_board', kwargs={'sprint_slug': self.sprint.slug})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)


class GhostPlanExcludedFromBoardTest(TestCase):
    """A plan whose owner has no enrollment must not appear on the board."""

    @classmethod
    def setUpTestData(cls):
        cls.sprint = Sprint.objects.create(
            name='May 2026', slug='may-2026',
            start_date=datetime.date(2026, 5, 1),
        )
        cls.viewer = User.objects.create_user(
            email='v@test.com', password='pw',
        )
        Plan.objects.create(
            member=cls.viewer, sprint=cls.sprint, visibility='cohort',
        )
        cls.ghost = User.objects.create_user(
            email='ghost@test.com', password='pw',
        )
        cls.ghost_plan = Plan.objects.create(
            member=cls.ghost, sprint=cls.sprint, visibility='cohort',
        )
        # Plan creation auto-enrolls; remove the ghost enrollment to
        # simulate "staff DELETE'd this enrollment, but the plan stayed
        # at cohort visibility (e.g. a test injection or stale data)".
        SprintEnrollment.objects.filter(
            sprint=cls.sprint, user=cls.ghost,
        ).delete()

    def test_ghost_plan_not_in_board_queryset(self):
        ids = set(
            Plan.objects.visible_on_cohort_board(
                sprint=self.sprint, viewer=self.viewer,
            ).values_list('pk', flat=True)
        )
        self.assertNotIn(self.ghost_plan.pk, ids)
