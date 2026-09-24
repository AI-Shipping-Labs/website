"""Tests for Cohort.mode (cohort/self_paced) — issue #1674.

Covers:
- Cohort.clean(): dated cohort requires start/end dates; self-paced must
  have no dates, no event_series, no max_participants.
- Partial unique constraint: at most one self-paced cohort per course.
- ensure_self_paced_cohort_enrollment(): idempotent implicit membership.
- decide_course_unit_drip_lock(): self-paced never locked (the bug this
  issue fixes) and the Unit -> leaf Module -> parent Module offset
  cascade.
- Every existing Cohort row keeps mode='cohort' with start/end untouched.
"""

import datetime

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from content.models import Cohort, CohortEnrollment, Course, Module, Unit
from content.services.course_units import decide_course_unit_drip_lock
from content.services.enrollment import ensure_self_paced_cohort_enrollment

User = get_user_model()


class CohortModeValidationTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(
            title='Course', slug='cohort-mode-course', status='published',
        )

    def test_dated_cohort_requires_both_dates(self):
        cohort = Cohort(course=self.course, name='C1', mode='cohort')
        with self.assertRaises(ValidationError):
            cohort.full_clean()

    def test_dated_cohort_with_both_dates_passes(self):
        cohort = Cohort(
            course=self.course, name='C1', mode='cohort',
            start_date=datetime.date(2026, 9, 21),
            end_date=datetime.date(2026, 11, 22),
        )
        cohort.full_clean()  # should not raise

    def test_self_paced_rejects_dates(self):
        cohort = Cohort(
            course=self.course, name='Self-paced', mode='self_paced',
            start_date=datetime.date(2026, 9, 21),
        )
        with self.assertRaises(ValidationError):
            cohort.full_clean()

    def test_self_paced_rejects_max_participants(self):
        cohort = Cohort(
            course=self.course, name='Self-paced', mode='self_paced',
            max_participants=50,
        )
        with self.assertRaises(ValidationError):
            cohort.full_clean()

    def test_self_paced_with_no_dates_no_capacity_passes(self):
        cohort = Cohort(course=self.course, name='Self-paced', mode='self_paced')
        cohort.full_clean()  # should not raise

    def test_second_self_paced_cohort_rejected(self):
        Cohort.objects.create(course=self.course, name='Self-paced', mode='self_paced')
        second = Cohort(course=self.course, name='Self-paced 2', mode='self_paced')
        with self.assertRaises(ValidationError):
            second.full_clean()

    def test_self_paced_on_different_course_is_fine(self):
        Cohort.objects.create(course=self.course, name='Self-paced', mode='self_paced')
        other_course = Course.objects.create(
            title='Other', slug='other-cohort-course', status='published',
        )
        other = Cohort(course=other_course, name='Self-paced', mode='self_paced')
        other.full_clean()  # should not raise


class CohortModeBackwardCompatibilityTest(TestCase):
    def test_existing_cohort_defaults_to_dated_mode(self):
        cohort = Cohort.objects.create(
            course=Course.objects.create(
                title='C', slug='legacy-cohort-course', status='published',
            ),
            name='March 2026 Cohort',
            start_date=datetime.date(2026, 3, 1),
            end_date=datetime.date(2026, 6, 1),
        )
        self.assertEqual(cohort.mode, 'cohort')
        self.assertEqual(cohort.start_date, datetime.date(2026, 3, 1))
        self.assertEqual(cohort.end_date, datetime.date(2026, 6, 1))


class EnsureSelfPacedCohortEnrollmentTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(
            title='Course', slug='self-paced-course', status='published',
        )
        cls.user = User.objects.create_user(email='learner@test.com', password='pw')

    def test_course_without_a_dated_cohort_gets_default_self_paced_membership(self):
        result = ensure_self_paced_cohort_enrollment(self.user, self.course)
        self.assertIsNotNone(result)
        self.assertEqual(result.cohort.mode, 'self_paced')
        self.assertEqual(result.cohort.name, 'Self-paced')
        self.assertTrue(CohortEnrollment.objects.filter(user=self.user).exists())

    def test_creates_enrollment_into_self_paced_cohort(self):
        self_paced = Cohort.objects.create(
            course=self.course, name='Self-paced', mode='self_paced',
        )
        enrollment = ensure_self_paced_cohort_enrollment(self.user, self.course)
        self.assertEqual(enrollment.cohort_id, self_paced.pk)

    def test_idempotent_no_duplicate_rows(self):
        Cohort.objects.create(course=self.course, name='Self-paced', mode='self_paced')
        ensure_self_paced_cohort_enrollment(self.user, self.course)
        ensure_self_paced_cohort_enrollment(self.user, self.course)
        self.assertEqual(
            CohortEnrollment.objects.filter(user=self.user, cohort__course=self.course).count(),
            1,
        )

    def test_user_already_in_dated_cohort_is_a_no_op(self):
        Cohort.objects.create(course=self.course, name='Self-paced', mode='self_paced')
        dated = Cohort.objects.create(
            course=self.course, name='Cohort 4', mode='cohort',
            start_date=datetime.date(2026, 9, 21), end_date=datetime.date(2026, 11, 22),
        )
        CohortEnrollment.objects.create(user=self.user, cohort=dated)
        ensure_self_paced_cohort_enrollment(self.user, self.course)
        self.assertFalse(
            CohortEnrollment.objects.filter(
                user=self.user, cohort__mode='self_paced',
            ).exists(),
        )

    def test_unenrolled_learner_is_not_auto_joined_to_a_real_cohort(self):
        dated = Cohort.objects.create(
            course=self.course, name='Cohort 4', mode='cohort',
            start_date=datetime.date(2026, 9, 21), end_date=datetime.date(2026, 11, 22),
        )

        result = ensure_self_paced_cohort_enrollment(self.user, self.course)

        self.assertIsNone(result)
        self.assertFalse(CohortEnrollment.objects.filter(user=self.user).exists())
        self.assertEqual(Cohort.objects.filter(course=self.course).count(), 1)
        self.assertEqual(Cohort.objects.get(course=self.course), dated)

    def test_anonymous_user_is_a_no_op(self):
        from django.contrib.auth.models import AnonymousUser
        Cohort.objects.create(course=self.course, name='Self-paced', mode='self_paced')
        result = ensure_self_paced_cohort_enrollment(AnonymousUser(), self.course)
        self.assertIsNone(result)

    def test_user_without_course_access_is_not_auto_enrolled(self):
        self.course.required_level = 30
        self.course.save(update_fields=['required_level'])

        result = ensure_self_paced_cohort_enrollment(self.user, self.course)

        self.assertIsNone(result)
        self.assertFalse(
            CohortEnrollment.objects.filter(user=self.user, cohort__course=self.course).exists(),
        )


class DripLockSelfPacedRegressionTest(TestCase):
    """Issue #1674: self-paced CohortEnrollment must not crash drip-lock."""

    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(
            title='Course', slug='drip-course', status='published', required_level=0,
        )
        cls.module = Module.objects.create(
            course=cls.course, title='Week 1', slug='week-1', sort_order=1,
        )
        cls.unit = Unit.objects.create(
            module=cls.module, title='Lesson', slug='lesson', sort_order=1,
            available_after_days=10,
        )
        cls.user = User.objects.create_user(email='sp@test.com', password='pw')

    def test_self_paced_cohort_enrollment_does_not_raise_and_is_unlocked(self):
        self_paced = Cohort.objects.create(
            course=self.course, name='Self-paced', mode='self_paced',
        )
        CohortEnrollment.objects.create(user=self.user, cohort=self_paced)
        # Would have raised TypeError (None + timedelta) against the
        # pre-fix code once a self-paced CohortEnrollment existed.
        decision = decide_course_unit_drip_lock(self.user, self.unit)
        self.assertFalse(decision.is_locked)

    def test_no_enrollment_at_all_stays_unlocked(self):
        decision = decide_course_unit_drip_lock(self.user, self.unit)
        self.assertFalse(decision.is_locked)

    def test_dated_cohort_enrollment_still_locks(self):
        cohort = Cohort.objects.create(
            course=self.course, name='Cohort 1', mode='cohort',
            start_date=datetime.date(2026, 1, 1), end_date=datetime.date(2026, 3, 1),
        )
        CohortEnrollment.objects.create(user=self.user, cohort=cohort)
        decision = decide_course_unit_drip_lock(
            self.user, self.unit, today=datetime.date(2026, 1, 5),
        )
        self.assertTrue(decision.is_locked)
        self.assertEqual(decision.available_date, datetime.date(2026, 1, 11))


class DripLockOffsetCascadeTest(TestCase):
    """Unit.available_after_days -> leaf Module's -> parent Module's."""

    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(
            title='Course', slug='cascade-course', status='published', required_level=0,
        )
        cls.week = Module.objects.create(
            course=cls.course, title='Week 4', slug='week-4', sort_order=1,
            available_after_days=21,
        )
        cls.submodule = Module.objects.create(
            course=cls.course, title='Topic', slug='topic', sort_order=1,
            parent=cls.week,
        )
        cls.submodule_no_offset = Module.objects.create(
            course=cls.course, title='Topic 2', slug='topic-2', sort_order=2,
            parent=cls.week,
        )
        cls.user = User.objects.create_user(email='cascade@test.com', password='pw')
        cls.cohort = Cohort.objects.create(
            course=cls.course, name='Cohort', mode='cohort',
            start_date=datetime.date(2026, 9, 21), end_date=datetime.date(2026, 11, 22),
        )
        CohortEnrollment.objects.create(user=cls.user, cohort=cls.cohort)

    def test_unit_override_wins_over_module_offset(self):
        unit = Unit.objects.create(
            module=self.submodule, title='U', slug='u', sort_order=1,
            available_after_days=3,
        )
        decision = decide_course_unit_drip_lock(
            self.user, unit, today=datetime.date(2026, 9, 22),
        )
        self.assertEqual(decision.available_date, datetime.date(2026, 9, 24))

    def test_falls_back_to_parent_module_offset_when_unit_and_leaf_unset(self):
        unit = Unit.objects.create(
            module=self.submodule, title='U2', slug='u2', sort_order=2,
        )
        decision = decide_course_unit_drip_lock(
            self.user, unit, today=datetime.date(2026, 9, 22),
        )
        # week.available_after_days=21 -> cohort.start_date + 21 days.
        self.assertEqual(decision.available_date, datetime.date(2026, 10, 12))

    def test_no_offset_anywhere_is_unlocked(self):
        leaf = Module.objects.create(
            course=self.course, title='No offset week', slug='no-offset-week',
            sort_order=2,
        )
        unit = Unit.objects.create(
            module=leaf, title='U3', slug='u3', sort_order=1,
        )
        decision = decide_course_unit_drip_lock(self.user, unit)
        self.assertFalse(decision.is_locked)
        self.assertIsNone(decision.available_date)
