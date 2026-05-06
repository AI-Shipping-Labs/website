"""Tests for ``Plan.objects.visible_on_cohort_board`` and
``Plan.objects.visible_to_member`` (issue #440).

Visibility filtering MUST happen at the queryset layer, not in
templates or view bodies. These tests pin down the exact semantics so
a future view that forgets to call the helper cannot leak private
plans -- the regression test in
``test_view_layer_no_visibility_literals.py`` enforces that views go
through these helpers.
"""

import datetime

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import TestCase

from plans.models import Plan, Sprint

User = get_user_model()


class VisibleOnCohortBoardTest(TestCase):
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
        cls.cohort_member_a = User.objects.create_user(
            email='a@test.com', password='pw',
        )
        cls.cohort_plan_a = Plan.objects.create(
            member=cls.cohort_member_a, sprint=cls.sprint,
            visibility='cohort',
        )
        cls.cohort_member_b = User.objects.create_user(
            email='b@test.com', password='pw',
        )
        cls.cohort_plan_b = Plan.objects.create(
            member=cls.cohort_member_b, sprint=cls.sprint,
            visibility='cohort',
        )
        cls.cohort_member_c = User.objects.create_user(
            email='c@test.com', password='pw',
        )
        cls.cohort_plan_c = Plan.objects.create(
            member=cls.cohort_member_c, sprint=cls.sprint,
            visibility='cohort',
        )
        cls.private_member_d = User.objects.create_user(
            email='d@test.com', password='pw',
        )
        cls.private_plan_d = Plan.objects.create(
            member=cls.private_member_d, sprint=cls.sprint,
            visibility='private',
        )
        cls.private_member_e = User.objects.create_user(
            email='e@test.com', password='pw',
        )
        cls.private_plan_e = Plan.objects.create(
            member=cls.private_member_e, sprint=cls.sprint,
            visibility='private',
        )

    def test_visible_on_cohort_board_empty_for_anonymous(self):
        result = Plan.objects.visible_on_cohort_board(
            sprint=self.sprint, viewer=AnonymousUser(),
        )
        self.assertEqual(result.count(), 0)

    def test_visible_on_cohort_board_empty_for_non_enrolled_user(self):
        """Non-enrolled users see nothing, even ``is_staff``.

        The cohort board is a member-scoped surface; staff who are NOT
        plan-holders in the sprint use Studio, not the board.
        """
        outsider = User.objects.create_user(
            email='outsider@test.com', password='pw', is_staff=True,
        )
        result = Plan.objects.visible_on_cohort_board(
            sprint=self.sprint, viewer=outsider,
        )
        self.assertEqual(result.count(), 0)

    def test_visible_on_cohort_board_excludes_private_plans(self):
        ids = set(
            Plan.objects.visible_on_cohort_board(
                sprint=self.sprint, viewer=self.viewer,
            ).values_list('pk', flat=True)
        )
        self.assertEqual(
            ids,
            {
                self.cohort_plan_a.pk,
                self.cohort_plan_b.pk,
                self.cohort_plan_c.pk,
            },
        )

    def test_visible_on_cohort_board_excludes_viewer_own_plan(self):
        ids = set(
            Plan.objects.visible_on_cohort_board(
                sprint=self.sprint, viewer=self.viewer,
            ).values_list('pk', flat=True)
        )
        self.assertNotIn(self.viewer_plan.pk, ids)

    def test_visible_on_cohort_board_includes_other_members_cohort_plans(self):
        ids = set(
            Plan.objects.visible_on_cohort_board(
                sprint=self.sprint, viewer=self.viewer,
            ).values_list('pk', flat=True)
        )
        self.assertIn(self.cohort_plan_a.pk, ids)
        self.assertIn(self.cohort_plan_b.pk, ids)
        self.assertIn(self.cohort_plan_c.pk, ids)


class VisibleToMemberTest(TestCase):
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
        cls.owner = User.objects.create_user(
            email='owner@test.com', password='pw',
        )
        cls.private_plan = Plan.objects.create(
            member=cls.owner, sprint=cls.sprint, visibility='private',
        )
        cls.cohort_owner = User.objects.create_user(
            email='cowner@test.com', password='pw',
        )
        cls.cohort_plan = Plan.objects.create(
            member=cls.cohort_owner, sprint=cls.sprint,
            visibility='cohort',
        )
        cls.teammate = User.objects.create_user(
            email='team@test.com', password='pw',
        )
        cls.teammate_plan = Plan.objects.create(
            member=cls.teammate, sprint=cls.sprint, visibility='private',
        )
        cls.outsider = User.objects.create_user(
            email='outsider@test.com', password='pw',
        )
        cls.outsider_plan = Plan.objects.create(
            member=cls.outsider, sprint=cls.other_sprint,
            visibility='cohort',
        )

    def test_owner_can_see_private_plan(self):
        result = Plan.objects.visible_to_member(
            plan_id=self.private_plan.pk, viewer=self.owner,
        )
        self.assertEqual(list(result), [self.private_plan])

    def test_other_member_can_see_cohort_plan(self):
        result = Plan.objects.visible_to_member(
            plan_id=self.cohort_plan.pk, viewer=self.teammate,
        )
        self.assertEqual(list(result), [self.cohort_plan])

    def test_other_member_blocked_from_private_plan(self):
        result = Plan.objects.visible_to_member(
            plan_id=self.private_plan.pk, viewer=self.teammate,
        )
        self.assertEqual(list(result), [])

    def test_other_sprint_blocked(self):
        """A user enrolled only in a different sprint cannot see the plan."""
        result = Plan.objects.visible_to_member(
            plan_id=self.cohort_plan.pk, viewer=self.outsider,
        )
        self.assertEqual(list(result), [])

    def test_anonymous_blocked(self):
        result = Plan.objects.visible_to_member(
            plan_id=self.cohort_plan.pk, viewer=AnonymousUser(),
        )
        self.assertEqual(list(result), [])
