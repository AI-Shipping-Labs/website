"""Tests for Course Cohorts - issue #81.

Covers:
- Cohort and CohortEnrollment model fields and constraints
- Cohort properties: enrollment_count, is_full, spots_remaining
- Course detail page shows active cohorts
- Enrollment API: POST /api/courses/{slug}/cohorts/{id}/enroll
- Unenrollment API: POST /api/courses/{slug}/cohorts/{id}/unenroll
- Tier-based enrollment gating
- Capacity enforcement (max_participants)
- Drip schedule: available_after_days on Unit, locked until cohort.start_date + days
- Admin CRUD for cohorts
"""

import datetime
import json

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import TestCase, Client
from django.utils import timezone

from content.access import LEVEL_OPEN, LEVEL_BASIC, LEVEL_MAIN
from content.models import (
    Course, Module, Unit, Cohort, CohortEnrollment, UserCourseProgress,
)

User = get_user_model()


class TierSetupMixin:
    """Mixin that creates the four standard tiers."""

    @classmethod
    def setUpTestData(cls):
        from payments.models import Tier
        cls.free_tier, _ = Tier.objects.get_or_create(
            slug='free', defaults={'name': 'Free', 'level': 0},
        )
        cls.basic_tier, _ = Tier.objects.get_or_create(
            slug='basic', defaults={'name': 'Basic', 'level': 10},
        )
        cls.main_tier, _ = Tier.objects.get_or_create(
            slug='main', defaults={'name': 'Main', 'level': 20, 'price_eur_year': 99},
        )
        cls.premium_tier, _ = Tier.objects.get_or_create(
            slug='premium', defaults={'name': 'Premium', 'level': 30, 'price_eur_year': 199},
        )


# ============================================================
# Cohort Model Tests
# ============================================================


class CohortModelTest(TestCase):
    """Test Cohort model fields and methods."""

    def setUp(self):
        self.course = Course.objects.create(
            title='Test Course', slug='test-course', status='published',
        )

    def test_create_cohort(self):
        cohort = Cohort.objects.create(
            course=self.course,
            name='March 2026 Cohort',
            start_date=datetime.date(2026, 3, 1),
            end_date=datetime.date(2026, 6, 1),
        )
        self.assertEqual(cohort.name, 'March 2026 Cohort')
        self.assertEqual(cohort.course, self.course)
        self.assertTrue(cohort.is_active)
        self.assertIsNone(cohort.max_participants)

    def test_str(self):
        cohort = Cohort.objects.create(
            course=self.course,
            name='Spring Cohort',
            start_date=datetime.date(2026, 3, 1),
            end_date=datetime.date(2026, 6, 1),
        )
        self.assertEqual(str(cohort), 'Test Course - Spring Cohort')

    def test_ordering_by_start_date(self):
        Cohort.objects.create(
            course=self.course, name='Later',
            start_date=datetime.date(2026, 6, 1),
            end_date=datetime.date(2026, 9, 1),
        )
        Cohort.objects.create(
            course=self.course, name='Earlier',
            start_date=datetime.date(2026, 3, 1),
            end_date=datetime.date(2026, 6, 1),
        )
        cohorts = list(Cohort.objects.filter(course=self.course))
        self.assertEqual(cohorts[0].name, 'Earlier')
        self.assertEqual(cohorts[1].name, 'Later')

    def test_enrollment_count_empty(self):
        cohort = Cohort.objects.create(
            course=self.course, name='Empty',
            start_date=datetime.date(2026, 3, 1),
            end_date=datetime.date(2026, 6, 1),
        )
        self.assertEqual(cohort.enrollment_count, 0)

    def test_enrollment_count_with_users(self):
        cohort = Cohort.objects.create(
            course=self.course, name='Count Test',
            start_date=datetime.date(2026, 3, 1),
            end_date=datetime.date(2026, 6, 1),
        )
        user1 = User.objects.create_user(email='u1@test.com')
        user2 = User.objects.create_user(email='u2@test.com')
        CohortEnrollment.objects.create(cohort=cohort, user=user1)
        CohortEnrollment.objects.create(cohort=cohort, user=user2)
        self.assertEqual(cohort.enrollment_count, 2)

    def test_is_full_when_at_capacity(self):
        cohort = Cohort.objects.create(
            course=self.course, name='Full',
            start_date=datetime.date(2026, 3, 1),
            end_date=datetime.date(2026, 6, 1),
            max_participants=1,
        )
        user = User.objects.create_user(email='full@test.com')
        CohortEnrollment.objects.create(cohort=cohort, user=user)
        self.assertTrue(cohort.is_full)

    def test_is_not_full_below_capacity(self):
        cohort = Cohort.objects.create(
            course=self.course, name='Not Full',
            start_date=datetime.date(2026, 3, 1),
            end_date=datetime.date(2026, 6, 1),
            max_participants=10,
        )
        self.assertFalse(cohort.is_full)

    def test_is_not_full_when_no_limit(self):
        cohort = Cohort.objects.create(
            course=self.course, name='Unlimited',
            start_date=datetime.date(2026, 3, 1),
            end_date=datetime.date(2026, 6, 1),
            max_participants=None,
        )
        self.assertFalse(cohort.is_full)

    def test_spots_remaining(self):
        cohort = Cohort.objects.create(
            course=self.course, name='Spots',
            start_date=datetime.date(2026, 3, 1),
            end_date=datetime.date(2026, 6, 1),
            max_participants=5,
        )
        user = User.objects.create_user(email='spot@test.com')
        CohortEnrollment.objects.create(cohort=cohort, user=user)
        self.assertEqual(cohort.spots_remaining, 4)

    def test_spots_remaining_unlimited(self):
        cohort = Cohort.objects.create(
            course=self.course, name='Unlimited Spots',
            start_date=datetime.date(2026, 3, 1),
            end_date=datetime.date(2026, 6, 1),
            max_participants=None,
        )
        self.assertIsNone(cohort.spots_remaining)

    def test_cascade_delete_from_course(self):
        Cohort.objects.create(
            course=self.course, name='Delete Test',
            start_date=datetime.date(2026, 3, 1),
            end_date=datetime.date(2026, 6, 1),
        )
        self.course.delete()
        self.assertEqual(Cohort.objects.count(), 0)


# ============================================================
# CohortEnrollment Model Tests
# ============================================================


class CohortEnrollmentModelTest(TestCase):
    """Test CohortEnrollment model fields and constraints."""

    def setUp(self):
        self.course = Course.objects.create(
            title='Course', slug='course', status='published',
        )
        self.cohort = Cohort.objects.create(
            course=self.course, name='Test Cohort',
            start_date=datetime.date(2026, 3, 1),
            end_date=datetime.date(2026, 6, 1),
        )
        self.user = User.objects.create_user(email='enroll@test.com')

    def test_create_enrollment(self):
        enrollment = CohortEnrollment.objects.create(
            cohort=self.cohort, user=self.user,
        )
        self.assertIsNotNone(enrollment.enrolled_at)
        self.assertEqual(enrollment.cohort, self.cohort)
        self.assertEqual(enrollment.user, self.user)

    def test_str(self):
        enrollment = CohortEnrollment.objects.create(
            cohort=self.cohort, user=self.user,
        )
        self.assertEqual(str(enrollment), f'{self.user} - {self.cohort.name}')

    def test_unique_together_cohort_user(self):
        CohortEnrollment.objects.create(
            cohort=self.cohort, user=self.user,
        )
        with self.assertRaises(IntegrityError):
            CohortEnrollment.objects.create(
                cohort=self.cohort, user=self.user,
            )

    def test_user_can_enroll_in_different_cohorts(self):
        cohort2 = Cohort.objects.create(
            course=self.course, name='Another Cohort',
            start_date=datetime.date(2026, 6, 1),
            end_date=datetime.date(2026, 9, 1),
        )
        CohortEnrollment.objects.create(cohort=self.cohort, user=self.user)
        CohortEnrollment.objects.create(cohort=cohort2, user=self.user)
        self.assertEqual(CohortEnrollment.objects.filter(user=self.user).count(), 2)

    def test_cascade_delete_from_cohort(self):
        CohortEnrollment.objects.create(cohort=self.cohort, user=self.user)
        self.cohort.delete()
        self.assertEqual(CohortEnrollment.objects.count(), 0)

    def test_cascade_delete_from_user(self):
        CohortEnrollment.objects.create(cohort=self.cohort, user=self.user)
        self.user.delete()
        self.assertEqual(CohortEnrollment.objects.count(), 0)


# ============================================================
# Unit available_after_days field
# ============================================================


class UnitAvailableAfterDaysTest(TestCase):
    """Test the available_after_days field on Unit."""

    def setUp(self):
        self.course = Course.objects.create(
            title='Drip Course', slug='drip-course', status='published',
        )
        self.module = Module.objects.create(
            course=self.course, title='Module', sort_order=1,
        )

    def test_default_is_none(self):
        unit = Unit.objects.create(
            module=self.module, title='No Drip', sort_order=1,
        )
        self.assertIsNone(unit.available_after_days)

    def test_set_available_after_days(self):
        unit = Unit.objects.create(
            module=self.module, title='Drip Unit', sort_order=1,
            available_after_days=7,
        )
        self.assertEqual(unit.available_after_days, 7)

    def test_available_after_days_zero(self):
        unit = Unit.objects.create(
            module=self.module, title='Day Zero', sort_order=1,
            available_after_days=0,
        )
        self.assertEqual(unit.available_after_days, 0)


# ============================================================
# Course Detail View - Cohort Display
# ============================================================


class CourseDetailCohortDisplayTest(TierSetupMixin, TestCase):
    """Test that active cohorts show on course detail page."""

    def setUp(self):
        self.client = Client()
        self.course = Course.objects.create(
            title='Cohort Course', slug='cohort-course',
            status='published', required_level=LEVEL_MAIN,
        )
        self.module = Module.objects.create(
            course=self.course, title='Module', sort_order=1,
        )
        Unit.objects.create(
            module=self.module, title='Lesson 1', sort_order=1,
        )
        self.cohort = Cohort.objects.create(
            course=self.course, name='Spring 2026',
            start_date=datetime.date(2026, 3, 1),
            end_date=datetime.date(2026, 6, 1),
            is_active=True,
            max_participants=30,
        )

    def test_shows_active_cohort_name(self):
        response = self.client.get('/courses/cohort-course')
        self.assertContains(response, 'Next cohort')
        self.assertContains(response, 'Spring 2026')

    def test_shows_cohort_start_date(self):
        response = self.client.get('/courses/cohort-course')
        self.assertContains(response, 'March 1, 2026')

    def test_shows_spots_remaining(self):
        response = self.client.get('/courses/cohort-course')
        self.assertContains(response, '30 of 30 spots remaining')

    def test_hides_inactive_cohort(self):
        self.cohort.is_active = False
        self.cohort.save()
        response = self.client.get('/courses/cohort-course')
        self.assertNotContains(response, 'Spring 2026')

    def test_shows_enroll_button_for_authorized_user(self):
        user = User.objects.create_user(email='main@test.com', password='testpass')
        user.tier = self.main_tier
        user.save()
        self.client.login(email='main@test.com', password='testpass')
        response = self.client.get('/courses/cohort-course')
        self.assertContains(response, 'Enroll')

    def test_shows_enrolled_button_for_enrolled_user(self):
        user = User.objects.create_user(email='enrolled@test.com', password='testpass')
        user.tier = self.main_tier
        user.save()
        CohortEnrollment.objects.create(cohort=self.cohort, user=user)
        self.client.login(email='enrolled@test.com', password='testpass')
        response = self.client.get('/courses/cohort-course')
        self.assertContains(response, 'Enrolled')

    def test_shows_full_message_when_cohort_is_full(self):
        self.cohort.max_participants = 1
        self.cohort.save()
        existing_user = User.objects.create_user(email='existing@test.com')
        CohortEnrollment.objects.create(cohort=self.cohort, user=existing_user)

        # Login as a different authorized user
        user = User.objects.create_user(email='main@test.com', password='testpass')
        user.tier = self.main_tier
        user.save()
        self.client.login(email='main@test.com', password='testpass')
        response = self.client.get('/courses/cohort-course')
        self.assertContains(response, 'Cohort is full')

    def test_no_enroll_button_for_unauthorized_user(self):
        """Anonymous users should not see enroll/unenroll buttons."""
        response = self.client.get('/courses/cohort-course')
        self.assertNotContains(response, 'data-action="enroll"')

    def test_no_enroll_button_for_user_without_tier(self):
        """Users with insufficient tier should not see enroll button."""
        user = User.objects.create_user(email='basic@test.com', password='testpass')
        user.tier = self.basic_tier
        user.save()
        self.client.login(email='basic@test.com', password='testpass')
        response = self.client.get('/courses/cohort-course')
        self.assertNotContains(response, 'data-action="enroll"')


# ============================================================
# Enrollment API Tests
# ============================================================


class CohortEnrollApiTest(TierSetupMixin, TestCase):
    """Test POST /api/courses/{slug}/cohorts/{id}/enroll."""

    def setUp(self):
        self.client = Client()
        self.course = Course.objects.create(
            title='API Course', slug='api-course',
            status='published', required_level=LEVEL_MAIN,
        )
        self.cohort = Cohort.objects.create(
            course=self.course, name='API Cohort',
            start_date=datetime.date(2026, 3, 1),
            end_date=datetime.date(2026, 6, 1),
            is_active=True,
            max_participants=5,
        )

    def _login_main_user(self):
        user = User.objects.create_user(email='main@test.com', password='testpass')
        user.tier = self.main_tier
        user.save()
        self.client.login(email='main@test.com', password='testpass')
        return user

    def test_enroll_success(self):
        user = self._login_main_user()
        response = self.client.post(
            f'/api/courses/api-course/cohorts/{self.cohort.pk}/enroll',
        )
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertTrue(data['enrolled'])
        self.assertTrue(
            CohortEnrollment.objects.filter(cohort=self.cohort, user=user).exists()
        )

    def test_enroll_requires_authentication(self):
        response = self.client.post(
            f'/api/courses/api-course/cohorts/{self.cohort.pk}/enroll',
        )
        self.assertEqual(response.status_code, 401)

    def test_enroll_requires_tier(self):
        user = User.objects.create_user(email='basic@test.com', password='testpass')
        user.tier = self.basic_tier
        user.save()
        self.client.login(email='basic@test.com', password='testpass')
        response = self.client.post(
            f'/api/courses/api-course/cohorts/{self.cohort.pk}/enroll',
        )
        self.assertEqual(response.status_code, 403)
        data = json.loads(response.content)
        self.assertIn('Main', data['error'])

    def test_enroll_capacity_enforced(self):
        self.cohort.max_participants = 1
        self.cohort.save()
        # Fill the cohort
        existing_user = User.objects.create_user(email='existing@test.com')
        CohortEnrollment.objects.create(cohort=self.cohort, user=existing_user)

        user = self._login_main_user()
        response = self.client.post(
            f'/api/courses/api-course/cohorts/{self.cohort.pk}/enroll',
        )
        self.assertEqual(response.status_code, 409)
        data = json.loads(response.content)
        self.assertIn('full', data['error'].lower())

    def test_enroll_already_enrolled(self):
        user = self._login_main_user()
        CohortEnrollment.objects.create(cohort=self.cohort, user=user)
        response = self.client.post(
            f'/api/courses/api-course/cohorts/{self.cohort.pk}/enroll',
        )
        self.assertEqual(response.status_code, 409)
        data = json.loads(response.content)
        self.assertIn('Already enrolled', data['error'])

    def test_enroll_nonexistent_cohort_returns_404(self):
        self._login_main_user()
        response = self.client.post(
            '/api/courses/api-course/cohorts/99999/enroll',
        )
        self.assertEqual(response.status_code, 404)

    def test_enroll_inactive_cohort_returns_404(self):
        self.cohort.is_active = False
        self.cohort.save()
        self._login_main_user()
        response = self.client.post(
            f'/api/courses/api-course/cohorts/{self.cohort.pk}/enroll',
        )
        self.assertEqual(response.status_code, 404)

    def test_enroll_nonexistent_course_returns_404(self):
        self._login_main_user()
        response = self.client.post(
            f'/api/courses/nonexistent/cohorts/{self.cohort.pk}/enroll',
        )
        self.assertEqual(response.status_code, 404)

    def test_get_method_not_allowed(self):
        self._login_main_user()
        response = self.client.get(
            f'/api/courses/api-course/cohorts/{self.cohort.pk}/enroll',
        )
        self.assertEqual(response.status_code, 405)

    def test_enroll_unlimited_cohort(self):
        self.cohort.max_participants = None
        self.cohort.save()
        user = self._login_main_user()
        response = self.client.post(
            f'/api/courses/api-course/cohorts/{self.cohort.pk}/enroll',
        )
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertTrue(data['enrolled'])

    def test_enroll_free_course(self):
        """Any authenticated user can enroll in a free course cohort."""
        free_course = Course.objects.create(
            title='Free', slug='free-course',
            status='published', required_level=LEVEL_OPEN, is_free=True,
        )
        cohort = Cohort.objects.create(
            course=free_course, name='Free Cohort',
            start_date=datetime.date(2026, 3, 1),
            end_date=datetime.date(2026, 6, 1),
            is_active=True,
        )
        user = User.objects.create_user(email='freeuser@test.com', password='testpass')
        self.client.login(email='freeuser@test.com', password='testpass')
        response = self.client.post(
            f'/api/courses/free-course/cohorts/{cohort.pk}/enroll',
        )
        self.assertEqual(response.status_code, 200)


# ============================================================
# Unenrollment API Tests
# ============================================================


class CohortUnenrollApiTest(TierSetupMixin, TestCase):
    """Test POST /api/courses/{slug}/cohorts/{id}/unenroll."""

    def setUp(self):
        self.client = Client()
        self.course = Course.objects.create(
            title='Unenroll Course', slug='unenroll-course',
            status='published', required_level=LEVEL_MAIN,
        )
        self.cohort = Cohort.objects.create(
            course=self.course, name='Unenroll Cohort',
            start_date=datetime.date(2026, 3, 1),
            end_date=datetime.date(2026, 6, 1),
            is_active=True,
        )
        self.user = User.objects.create_user(email='main@test.com', password='testpass')
        self.user.tier = self.main_tier
        self.user.save()
        self.client.login(email='main@test.com', password='testpass')

    def test_unenroll_success(self):
        CohortEnrollment.objects.create(cohort=self.cohort, user=self.user)
        response = self.client.post(
            f'/api/courses/unenroll-course/cohorts/{self.cohort.pk}/unenroll',
        )
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertFalse(data['enrolled'])
        self.assertFalse(
            CohortEnrollment.objects.filter(cohort=self.cohort, user=self.user).exists()
        )

    def test_unenroll_not_enrolled_returns_404(self):
        response = self.client.post(
            f'/api/courses/unenroll-course/cohorts/{self.cohort.pk}/unenroll',
        )
        self.assertEqual(response.status_code, 404)

    def test_unenroll_requires_authentication(self):
        self.client.logout()
        response = self.client.post(
            f'/api/courses/unenroll-course/cohorts/{self.cohort.pk}/unenroll',
        )
        self.assertEqual(response.status_code, 401)

    def test_unenroll_nonexistent_cohort_returns_404(self):
        response = self.client.post(
            '/api/courses/unenroll-course/cohorts/99999/unenroll',
        )
        self.assertEqual(response.status_code, 404)

    def test_get_method_not_allowed(self):
        response = self.client.get(
            f'/api/courses/unenroll-course/cohorts/{self.cohort.pk}/unenroll',
        )
        self.assertEqual(response.status_code, 405)


# ============================================================
# Drip Schedule Tests
# ============================================================


class DripScheduleTest(TierSetupMixin, TestCase):
    """Test drip schedule: unit locked until cohort.start_date + available_after_days."""

    def setUp(self):
        self.client = Client()
        self.course = Course.objects.create(
            title='Drip Course', slug='drip-course',
            status='published', required_level=LEVEL_MAIN,
        )
        self.module = Module.objects.create(
            course=self.course, title='Module 1', sort_order=1,
        )
        # Unit available immediately (no drip)
        self.unit_no_drip = Unit.objects.create(
            module=self.module, title='Available Now', sort_order=1,
            body='Immediate content.',
        )
        # Unit with drip schedule
        self.unit_drip = Unit.objects.create(
            module=self.module, title='Week 2 Lesson', sort_order=2,
            body='Week 2 content.',
            available_after_days=14,
        )
        # User with access
        self.user = User.objects.create_user(email='main@test.com', password='testpass')
        self.user.tier = self.main_tier
        self.user.save()
        self.client.login(email='main@test.com', password='testpass')

    def test_no_drip_unit_accessible_without_cohort(self):
        """User not in a cohort can access units with available_after_days=None."""
        response = self.client.get('/courses/drip-course/1/1')
        self.assertEqual(response.status_code, 200)

    def test_drip_unit_accessible_without_cohort_enrollment(self):
        """User NOT enrolled in any cohort can access drip units (drip only applies to cohort members)."""
        response = self.client.get('/courses/drip-course/1/2')
        self.assertEqual(response.status_code, 200)

    def test_drip_unit_locked_when_too_early(self):
        """User enrolled in a cohort, unit locked because it is before start_date + days."""
        # Cohort starts far in the future
        cohort = Cohort.objects.create(
            course=self.course, name='Future Cohort',
            start_date=datetime.date(2030, 1, 1),
            end_date=datetime.date(2030, 6, 1),
            is_active=True,
        )
        CohortEnrollment.objects.create(cohort=cohort, user=self.user)
        response = self.client.get('/courses/drip-course/1/2')
        self.assertEqual(response.status_code, 403)
        self.assertContains(response, 'This lesson will be available on', status_code=403)

    def test_drip_unit_available_after_date_passes(self):
        """User enrolled in a cohort, unit available because start_date + days has passed."""
        # Cohort started long ago
        cohort = Cohort.objects.create(
            course=self.course, name='Past Cohort',
            start_date=datetime.date(2020, 1, 1),
            end_date=datetime.date(2020, 6, 1),
            is_active=True,
        )
        CohortEnrollment.objects.create(cohort=cohort, user=self.user)
        response = self.client.get('/courses/drip-course/1/2')
        self.assertEqual(response.status_code, 200)

    def test_drip_does_not_affect_no_drip_unit(self):
        """Unit without available_after_days is always accessible to cohort members."""
        cohort = Cohort.objects.create(
            course=self.course, name='Future Cohort',
            start_date=datetime.date(2030, 1, 1),
            end_date=datetime.date(2030, 6, 1),
            is_active=True,
        )
        CohortEnrollment.objects.create(cohort=cohort, user=self.user)
        response = self.client.get('/courses/drip-course/1/1')
        self.assertEqual(response.status_code, 200)

    def test_drip_locked_shows_clock_icon(self):
        """Drip-locked unit should show clock icon, not lock icon."""
        cohort = Cohort.objects.create(
            course=self.course, name='Future Cohort',
            start_date=datetime.date(2030, 1, 1),
            end_date=datetime.date(2030, 6, 1),
            is_active=True,
        )
        CohortEnrollment.objects.create(cohort=cohort, user=self.user)
        response = self.client.get('/courses/drip-course/1/2')
        self.assertContains(response, 'clock', status_code=403)

    def test_drip_unit_available_after_days_zero(self):
        """Unit with available_after_days=0 is available from day 1."""
        unit_day0 = Unit.objects.create(
            module=self.module, title='Day Zero', sort_order=3,
            body='Day zero content.',
            available_after_days=0,
        )
        # Cohort starts today
        cohort = Cohort.objects.create(
            course=self.course, name='Today Cohort',
            start_date=timezone.now().date(),
            end_date=timezone.now().date() + datetime.timedelta(days=90),
            is_active=True,
        )
        CohortEnrollment.objects.create(cohort=cohort, user=self.user)
        response = self.client.get('/courses/drip-course/1/3')
        self.assertEqual(response.status_code, 200)


# ============================================================
# Admin Tests
# ============================================================


class CohortAdminTest(TestCase):
    """Test admin CRUD for cohorts."""

    def setUp(self):
        self.client = Client()
        self.admin_user = User.objects.create_superuser(
            email='admin@test.com', password='testpass',
        )
        self.client.login(email='admin@test.com', password='testpass')
        self.course = Course.objects.create(
            title='Admin Course', slug='admin-course', status='published',
        )

    def test_admin_cohort_list(self):
        Cohort.objects.create(
            course=self.course, name='Admin Cohort',
            start_date=datetime.date(2026, 3, 1),
            end_date=datetime.date(2026, 6, 1),
        )
        response = self.client.get('/admin/content/cohort/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Admin Cohort')

    def test_admin_cohort_add_page(self):
        response = self.client.get('/admin/content/cohort/add/')
        self.assertEqual(response.status_code, 200)

    def test_admin_cohort_change_page(self):
        cohort = Cohort.objects.create(
            course=self.course, name='Change Test',
            start_date=datetime.date(2026, 3, 1),
            end_date=datetime.date(2026, 6, 1),
        )
        response = self.client.get(f'/admin/content/cohort/{cohort.pk}/change/')
        self.assertEqual(response.status_code, 200)

    def test_admin_cohort_enrollment_list(self):
        response = self.client.get('/admin/content/cohortenrollment/')
        self.assertEqual(response.status_code, 200)

    def test_admin_cohort_inline_on_course(self):
        """Cohort inline should appear on course edit page."""
        response = self.client.get(f'/admin/content/course/{self.course.pk}/change/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'cohorts-')

    def test_admin_cohort_shows_enrollment_count(self):
        cohort = Cohort.objects.create(
            course=self.course, name='Count Cohort',
            start_date=datetime.date(2026, 3, 1),
            end_date=datetime.date(2026, 6, 1),
        )
        user = User.objects.create_user(email='u@test.com')
        CohortEnrollment.objects.create(cohort=cohort, user=user)
        response = self.client.get('/admin/content/cohort/')
        self.assertEqual(response.status_code, 200)

    def test_admin_unit_has_available_after_days_field(self):
        """Unit admin should show the available_after_days field."""
        module = Module.objects.create(
            course=self.course, title='M1', sort_order=1,
        )
        unit = Unit.objects.create(
            module=module, title='U1', sort_order=1,
        )
        response = self.client.get(f'/admin/content/unit/{unit.pk}/change/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'available_after_days')
