"""Tests for the SprintEnrollment model + Plan post_save signal (issue #443).

These tests cover the data invariants:

- ``SprintEnrollment`` enforces unique ``(sprint, user)``.
- ``enrolled_by`` survives staff-user deletion (``SET_NULL``).
- The ``Plan`` post-save signal back-creates an enrollment exactly once.
- The pre-existing ``Plan`` rows in legacy migrations get back-filled
  by the data migration -- proven indirectly via the cohort-board
  tests in ``test_views_cohort_board.py`` that still pass without
  modification.
"""

import datetime

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase

from plans.models import Plan, Sprint, SprintEnrollment

User = get_user_model()


class SprintEnrollmentConstraintsTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.sprint = Sprint.objects.create(
            name='May 2026', slug='may-2026',
            start_date=datetime.date(2026, 5, 1),
        )
        cls.user = User.objects.create_user(
            email='m@test.com', password='pw',
        )

    def test_unique_sprint_user_constraint(self):
        SprintEnrollment.objects.create(sprint=self.sprint, user=self.user)
        # The unique constraint on (sprint, user) must reject a duplicate.
        with self.assertRaises(IntegrityError), transaction.atomic():
            SprintEnrollment.objects.create(sprint=self.sprint, user=self.user)

    def test_enrolled_by_survives_staff_deletion(self):
        """SET_NULL means deleting the staff user keeps the audit row."""
        staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        enrollment = SprintEnrollment.objects.create(
            sprint=self.sprint, user=self.user, enrolled_by=staff,
        )
        staff_id = staff.pk
        staff.delete()
        enrollment.refresh_from_db()
        self.assertIsNone(enrollment.enrolled_by_id)
        # The audit row is still present.
        self.assertTrue(
            SprintEnrollment.objects.filter(pk=enrollment.pk).exists(),
        )
        # And the staff user is genuinely gone.
        self.assertFalse(User.objects.filter(pk=staff_id).exists())


class PlanPostSaveSignalTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.sprint = Sprint.objects.create(
            name='May 2026', slug='may-2026',
            start_date=datetime.date(2026, 5, 1),
        )
        cls.member = User.objects.create_user(
            email='m@test.com', password='pw',
        )

    def test_plan_create_back_creates_enrollment(self):
        # Sanity: no enrollment yet.
        self.assertFalse(
            SprintEnrollment.objects.filter(
                sprint=self.sprint, user=self.member,
            ).exists()
        )
        Plan.objects.create(member=self.member, sprint=self.sprint)
        self.assertTrue(
            SprintEnrollment.objects.filter(
                sprint=self.sprint, user=self.member, enrolled_by__isnull=True,
            ).exists()
        )

    def test_plan_save_does_not_duplicate_enrollment(self):
        """Re-saving an existing plan must NOT create a second enrollment."""
        plan = Plan.objects.create(member=self.member, sprint=self.sprint)
        plan.status = 'active'
        plan.save(update_fields=['status', 'updated_at'])
        self.assertEqual(
            SprintEnrollment.objects.filter(
                sprint=self.sprint, user=self.member,
            ).count(),
            1,
        )

    def test_signal_does_not_duplicate_pre_existing_enrollment(self):
        """Self-join then plan-create must not duplicate the enrollment row."""
        SprintEnrollment.objects.create(
            sprint=self.sprint, user=self.member,
        )
        Plan.objects.create(member=self.member, sprint=self.sprint)
        self.assertEqual(
            SprintEnrollment.objects.filter(
                sprint=self.sprint, user=self.member,
            ).count(),
            1,
        )
