"""Direct tests for course-unit access and context services."""

import datetime

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import TestCase
from django.utils import timezone

from content.access import LEVEL_MAIN, LEVEL_OPEN, LEVEL_REGISTERED
from content.models import (
    Cohort,
    CohortEnrollment,
    Course,
    CourseAccess,
    Module,
    Unit,
    UserCourseProgress,
)
from content.services.course_units import (
    ACCESS_DENIED_AUTHENTICATION,
    ACCESS_DENIED_INSUFFICIENT_TIER,
    ACCESS_DENIED_LEGACY_SIGNIN,
    ACCESS_DENIED_UNVERIFIED_EMAIL,
    ACCESS_GRANTED,
    ACCESS_GRANTED_PREVIEW,
    build_course_unit_navigation_context,
    build_gated_course_unit_context,
    decide_course_unit_access,
    decide_course_unit_drip_lock,
)
from tests.fixtures import TierSetupMixin

User = get_user_model()


class CourseUnitAccessDecisionTest(TierSetupMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.course = Course.objects.create(
            title='Policy Course',
            slug='policy-course',
            status='published',
            required_level=LEVEL_MAIN,
        )
        cls.module = Module.objects.create(
            course=cls.course,
            title='Module',
            slug='module',
            sort_order=1,
        )
        cls.unit = Unit.objects.create(
            module=cls.module,
            title='Paid Lesson',
            slug='paid-lesson',
            sort_order=1,
            body='Paid lesson body.',
            homework='First homework sentence. Hidden second sentence.',
        )
        cls.preview_unit = Unit.objects.create(
            module=cls.module,
            title='Preview',
            slug='preview',
            sort_order=2,
            body='Preview body.',
            is_preview=True,
        )
        cls.open_override_unit = Unit.objects.create(
            module=cls.module,
            title='Open Override',
            slug='open-override',
            sort_order=3,
            body='Open body.',
            required_level=LEVEL_OPEN,
        )

    def _user(self, email, tier, *, verified=True):
        user = User.objects.create_user(email=email, email_verified=verified)
        user.tier = tier
        user.save(update_fields=['tier'])
        return user

    def test_preview_unit_allows_anonymous(self):
        decision = decide_course_unit_access(AnonymousUser(), self.preview_unit)

        self.assertTrue(decision.has_access)
        self.assertEqual(decision.reason, ACCESS_GRANTED_PREVIEW)
        self.assertEqual(decision.status_code, 200)

    def test_explicit_open_unit_allows_anonymous_on_paid_course(self):
        decision = decide_course_unit_access(
            AnonymousUser(),
            self.open_override_unit,
        )

        self.assertTrue(decision.has_access)
        self.assertEqual(decision.reason, ACCESS_GRANTED)

    def test_legacy_open_course_unit_denies_anonymous_with_signin_reason(self):
        course = Course.objects.create(
            title='Legacy Free',
            slug='legacy-free-policy',
            status='published',
            required_level=LEVEL_OPEN,
        )
        module = Module.objects.create(
            course=course,
            title='M',
            slug='m',
            sort_order=1,
        )
        unit = Unit.objects.create(
            module=module,
            title='Legacy Unit',
            slug='legacy-unit',
            sort_order=1,
        )

        decision = decide_course_unit_access(AnonymousUser(), unit)
        context = build_gated_course_unit_context(
            AnonymousUser(), course, module, unit, decision,
        )

        self.assertFalse(decision.has_access)
        self.assertEqual(decision.reason, ACCESS_DENIED_LEGACY_SIGNIN)
        self.assertEqual(decision.gated_reason, '')
        self.assertEqual(decision.status_code, 403)
        self.assertEqual(context['cta_label'], 'Sign Up')
        self.assertEqual(context['gated_cta_label'], 'Sign Up')

    def test_registered_unit_denies_anonymous_with_authentication_reason(self):
        course = Course.objects.create(
            title='Registered',
            slug='registered-policy',
            status='published',
            required_level=LEVEL_OPEN,
            default_unit_required_level=LEVEL_REGISTERED,
        )
        module = Module.objects.create(
            course=course,
            title='M',
            slug='m',
            sort_order=1,
        )
        unit = Unit.objects.create(
            module=module,
            title='Registered Unit',
            slug='registered-unit',
            sort_order=1,
        )

        decision = decide_course_unit_access(AnonymousUser(), unit)
        context = build_gated_course_unit_context(
            AnonymousUser(), course, module, unit, decision,
        )

        self.assertFalse(decision.has_access)
        self.assertEqual(decision.reason, ACCESS_DENIED_AUTHENTICATION)
        self.assertEqual(decision.gated_reason, ACCESS_DENIED_AUTHENTICATION)
        self.assertEqual(context['cta_label'], 'Sign In')
        self.assertEqual(context['signup_cta_label'], 'Create a free account')
        self.assertIn('/accounts/login/?next=', context['gated_cta_url'])

    def test_registered_unit_allows_verified_free_user(self):
        self.course.default_unit_required_level = LEVEL_REGISTERED
        self.course.save(update_fields=['default_unit_required_level'])
        user = self._user('verified-free@policy.test', self.free_tier)

        decision = decide_course_unit_access(user, self.unit)

        self.assertTrue(decision.has_access)
        self.assertEqual(decision.reason, ACCESS_GRANTED)

    def test_registered_unit_denies_unverified_free_user_with_verify_status(self):
        self.course.default_unit_required_level = LEVEL_REGISTERED
        self.course.save(update_fields=['default_unit_required_level'])
        user = self._user(
            'unverified-free@policy.test',
            self.free_tier,
            verified=False,
        )

        decision = decide_course_unit_access(user, self.unit)
        context = build_gated_course_unit_context(
            user, self.course, self.module, self.unit, decision,
        )

        self.assertFalse(decision.has_access)
        self.assertEqual(decision.reason, ACCESS_DENIED_UNVERIFIED_EMAIL)
        self.assertEqual(decision.status_code, 200)
        self.assertEqual(context['gated_reason'], ACCESS_DENIED_UNVERIFIED_EMAIL)
        self.assertEqual(context['verify_email_address'], user.email)

    def test_paid_unit_denies_basic_user_with_insufficient_tier_reason(self):
        user = self._user('basic@policy.test', self.basic_tier)

        decision = decide_course_unit_access(user, self.unit)
        context = build_gated_course_unit_context(
            user, self.course, self.module, self.unit, decision,
        )

        self.assertFalse(decision.has_access)
        self.assertEqual(decision.reason, ACCESS_DENIED_INSUFFICIENT_TIER)
        self.assertEqual(decision.effective_level, LEVEL_MAIN)
        self.assertEqual(decision.status_code, 403)
        self.assertEqual(context['cta_message'], 'Upgrade to Main to access this lesson')
        self.assertEqual(context['current_user_state'], 'Current access: Basic member')

    def test_course_access_grant_allows_paid_unit(self):
        user = self._user('granted@policy.test', self.free_tier)
        CourseAccess.objects.create(user=user, course=self.course)

        decision = decide_course_unit_access(user, self.unit)

        self.assertTrue(decision.has_access)
        self.assertEqual(decision.reason, ACCESS_GRANTED)


class CourseUnitDripDecisionTest(TierSetupMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.course = Course.objects.create(
            title='Drip Policy',
            slug='drip-policy',
            status='published',
            required_level=LEVEL_MAIN,
        )
        self.module = Module.objects.create(
            course=self.course,
            title='M',
            slug='m',
            sort_order=1,
        )
        self.unit = Unit.objects.create(
            module=self.module,
            title='Week Two',
            slug='week-two',
            sort_order=1,
            available_after_days=14,
        )
        self.user = User.objects.create_user(email='main@policy.test')
        self.user.tier = self.main_tier
        self.user.save(update_fields=['tier'])

    def test_drip_lock_decision_is_locked_before_available_date(self):
        cohort = Cohort.objects.create(
            course=self.course,
            name='Future',
            start_date=timezone.now().date() + datetime.timedelta(days=10),
            end_date=timezone.now().date() + datetime.timedelta(days=90),
            is_active=True,
        )
        CohortEnrollment.objects.create(cohort=cohort, user=self.user)

        decision = decide_course_unit_drip_lock(
            self.user,
            self.unit,
            today=timezone.now().date(),
        )

        self.assertTrue(decision.is_locked)
        self.assertEqual(
            decision.available_date,
            cohort.start_date + datetime.timedelta(days=14),
        )

    def test_drip_lock_decision_is_unlocked_without_active_enrollment(self):
        decision = decide_course_unit_drip_lock(self.user, self.unit)

        self.assertFalse(decision.is_locked)
        self.assertIsNone(decision.available_date)


class CourseUnitNavigationContextTest(TierSetupMixin, TestCase):
    def test_navigation_context_contains_progress_and_neighbors(self):
        course = Course.objects.create(
            title='Nav Policy',
            slug='nav-policy',
            status='published',
            required_level=LEVEL_OPEN,
        )
        module = Module.objects.create(
            course=course,
            title='M',
            slug='m',
            sort_order=1,
        )
        first = Unit.objects.create(
            module=module,
            title='First',
            slug='first',
            sort_order=1,
        )
        second = Unit.objects.create(
            module=module,
            title='Second',
            slug='second',
            sort_order=2,
        )
        user = User.objects.create_user(
            email='nav@policy.test',
            email_verified=True,
        )
        UserCourseProgress.objects.create(
            user=user,
            unit=first,
            completed_at=timezone.now(),
        )

        context = build_course_unit_navigation_context(user, course, module, second)

        self.assertEqual(context['prev_unit'], first)
        self.assertIsNone(context['next_unit'])
        self.assertEqual(context['completed_unit_ids'], {first.pk})
        self.assertFalse(context['is_completed'])
        self.assertEqual(context['reader_progress_current'], 2)
        self.assertEqual(context['reader_progress_total'], 2)
        self.assertEqual(context['reader_progress_completed'], 1)
