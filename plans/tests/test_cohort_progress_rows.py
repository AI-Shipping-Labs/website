"""Tests for ``Plan.objects.cohort_progress_rows`` (issue #461).

The progress board sibling to ``visible_on_cohort_board``: returns every
enrolled member's plan regardless of visibility, annotated with progress
counts. Visibility filtering happens in the view's row classifier
(``plans.cohort_rows``), not in this queryset.
"""

import datetime

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import TestCase
from django.utils import timezone

from plans.models import Checkpoint, Plan, Sprint, SprintEnrollment, Week

User = get_user_model()


class CohortProgressRowsTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.sprint = Sprint.objects.create(
            name='May 2026', slug='may-2026',
            start_date=datetime.date(2026, 5, 1),
        )
        cls.other_sprint = Sprint.objects.create(
            name='June 2026', slug='june-2026',
            start_date=datetime.date(2026, 6, 1),
        )
        cls.viewer = User.objects.create_user(
            email='viewer@test.com', password='pw',
        )
        cls.viewer_plan = Plan.objects.create(
            member=cls.viewer, sprint=cls.sprint, visibility='cohort',
        )
        cls.cohort_member = User.objects.create_user(
            email='cohort@test.com', password='pw',
        )
        cls.cohort_plan = Plan.objects.create(
            member=cls.cohort_member, sprint=cls.sprint,
            visibility='cohort',
        )
        cls.private_member = User.objects.create_user(
            email='private@test.com', password='pw',
        )
        cls.private_plan = Plan.objects.create(
            member=cls.private_member, sprint=cls.sprint,
            visibility='private',
        )
        cls.outsider = User.objects.create_user(
            email='outsider@test.com', password='pw',
        )
        cls.outsider_plan = Plan.objects.create(
            member=cls.outsider, sprint=cls.other_sprint,
            visibility='cohort',
        )

    def test_returns_empty_queryset_for_anonymous_viewer(self):
        result = Plan.objects.cohort_progress_rows(
            sprint=self.sprint, viewer=AnonymousUser(),
        )
        self.assertEqual(result.count(), 0)

    def test_returns_empty_queryset_for_unauthenticated_none(self):
        result = Plan.objects.cohort_progress_rows(
            sprint=self.sprint, viewer=None,
        )
        self.assertEqual(result.count(), 0)

    def test_returns_empty_queryset_for_non_enrolled_staff(self):
        """Non-enrolled staff get nothing -- the board is member-scoped.

        This mirrors :meth:`visible_on_cohort_board` and matches the
        view's 404 for non-enrolled staff. Staff use Studio for full
        access; the cohort progress board is the member surface.
        """
        non_enrolled_staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        result = Plan.objects.cohort_progress_rows(
            sprint=self.sprint, viewer=non_enrolled_staff,
        )
        self.assertEqual(result.count(), 0)

    def test_returns_plans_of_every_visibility_for_enrolled_viewer(self):
        """Both cohort and private plans appear -- visibility is NOT filtered."""
        ids = set(
            Plan.objects.cohort_progress_rows(
                sprint=self.sprint, viewer=self.viewer,
            ).values_list('pk', flat=True)
        )
        self.assertEqual(
            ids,
            {
                self.viewer_plan.pk,
                self.cohort_plan.pk,
                self.private_plan.pk,
            },
        )

    def test_includes_viewer_own_plan(self):
        """Sibling helper INCLUDES the viewer's own plan; the view excludes it.

        Keeping the viewer's row in the queryset lets ``cohort_progress_rows``
        be reused by other surfaces (e.g. a future cohort summary card).
        """
        ids = set(
            Plan.objects.cohort_progress_rows(
                sprint=self.sprint, viewer=self.viewer,
            ).values_list('pk', flat=True)
        )
        self.assertIn(self.viewer_plan.pk, ids)

    def test_excludes_plans_in_other_sprints(self):
        ids = set(
            Plan.objects.cohort_progress_rows(
                sprint=self.sprint, viewer=self.viewer,
            ).values_list('pk', flat=True)
        )
        self.assertNotIn(self.outsider_plan.pk, ids)

    def test_excludes_plans_whose_owner_lost_their_enrollment(self):
        """Ghost plan invariant: a plan with no matching enrollment is dropped.

        Same rule as ``visible_on_cohort_board`` (issue #443).
        """
        ghost = User.objects.create_user(
            email='ghost@test.com', password='pw',
        )
        ghost_plan = Plan.objects.create(
            member=ghost, sprint=self.sprint, visibility='cohort',
        )
        # Plan-create back-creates the enrollment via signal; remove it
        # to simulate a staff-driven enrollment delete.
        SprintEnrollment.objects.filter(
            sprint=self.sprint, user=ghost,
        ).delete()

        ids = set(
            Plan.objects.cohort_progress_rows(
                sprint=self.sprint, viewer=self.viewer,
            ).values_list('pk', flat=True)
        )
        self.assertNotIn(ghost_plan.pk, ids)


class CohortProgressRowsAnnotationTest(TestCase):
    """Verify ``progress_total`` and ``progress_done`` annotations."""

    @classmethod
    def setUpTestData(cls):
        cls.sprint = Sprint.objects.create(
            name='May 2026', slug='may-2026',
            start_date=datetime.date(2026, 5, 1),
        )
        cls.viewer = User.objects.create_user(
            email='viewer@test.com', password='pw',
        )
        Plan.objects.create(
            member=cls.viewer, sprint=cls.sprint, visibility='cohort',
        )
        cls.teammate = User.objects.create_user(
            email='teammate@test.com', password='pw',
        )
        cls.plan = Plan.objects.create(
            member=cls.teammate, sprint=cls.sprint, visibility='cohort',
        )
        # Two weeks, 5 checkpoints total, 3 done.
        week1 = Week.objects.create(plan=cls.plan, week_number=1)
        Checkpoint.objects.create(
            week=week1, description='cp1', done_at=timezone.now(),
        )
        Checkpoint.objects.create(
            week=week1, description='cp2', done_at=timezone.now(),
        )
        Checkpoint.objects.create(week=week1, description='cp3')
        week2 = Week.objects.create(plan=cls.plan, week_number=2)
        Checkpoint.objects.create(
            week=week2, description='cp4', done_at=timezone.now(),
        )
        Checkpoint.objects.create(week=week2, description='cp5')

    def test_progress_total_counts_all_checkpoints(self):
        plan = (
            Plan.objects.cohort_progress_rows(
                sprint=self.sprint, viewer=self.viewer,
            )
            .get(pk=self.plan.pk)
        )
        self.assertEqual(plan.progress_total, 5)

    def test_progress_done_counts_only_done_checkpoints(self):
        plan = (
            Plan.objects.cohort_progress_rows(
                sprint=self.sprint, viewer=self.viewer,
            )
            .get(pk=self.plan.pk)
        )
        self.assertEqual(plan.progress_done, 3)
