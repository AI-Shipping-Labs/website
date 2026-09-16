"""Tests for entitlement-mode course access (issue #1658).

Covers the full access decision matrix for courses sold outside the
membership plans (e.g. the Maven buildcamp):

- Course model fields (access_mode, enroll_url, program_label,
  is_entitlement_mode)
- can_access() across the decision matrix: anonymous, free, basic, main,
  premium, entitled non-subscriber, entitled subscriber, staff — for both
  Course and Unit content
- get_gated_reason() returns 'entitlement_required' (not 'insufficient_tier')
- build_gated_access_copy()/build_gating_context() entitlement copy
- Regression: tier-mode courses are completely unaffected
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import TestCase, tag

from content.access import (
    LEVEL_MAIN,
    LEVEL_OPEN,
    LEVEL_REGISTERED,
    build_gated_access_copy,
    build_gating_context,
    can_access,
    get_gated_reason,
)
from content.models import Course, CourseAccess, Module, Unit
from tests.fixtures import TierSetupMixin, set_membership

User = get_user_model()


def _make_entitlement_course(**overrides):
    defaults = dict(
        title='AI Engineering Buildcamp',
        slug='ai-engineering-buildcamp',
        status='published',
        required_level=LEVEL_MAIN,
        access_mode='entitlement',
        enroll_url='https://maven.com/alexey-grigorev/from-rag-to-agents',
        program_label='Maven',
    )
    defaults.update(overrides)
    return Course.objects.create(**defaults)


class CourseAccessModeModelTest(TestCase):
    """Model-level coverage: fields, defaults, and is_entitlement_mode."""

    def test_default_access_mode_is_tier(self):
        course = Course.objects.create(title='Plain', slug='plain')
        self.assertEqual(course.access_mode, 'tier')
        self.assertFalse(course.is_entitlement_mode)

    def test_enroll_url_and_program_label_default_blank(self):
        course = Course.objects.create(title='Plain', slug='plain2')
        self.assertEqual(course.enroll_url, '')
        self.assertEqual(course.program_label, '')

    def test_entitlement_mode_sets_is_entitlement_mode_true(self):
        course = _make_entitlement_course()
        self.assertTrue(course.is_entitlement_mode)

    def test_entitlement_course_stores_enroll_url_and_program_label(self):
        course = _make_entitlement_course()
        self.assertEqual(
            course.enroll_url,
            'https://maven.com/alexey-grigorev/from-rag-to-agents',
        )
        self.assertEqual(course.program_label, 'Maven')


@tag('core')
class EntitlementAccessDecisionMatrixTest(TierSetupMixin, TestCase):
    """The full access decision matrix (issue #1658 critical regression case)."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.course = _make_entitlement_course()
        cls.module = Module.objects.create(
            course=cls.course, title='Module', slug='module', sort_order=1,
        )
        cls.unit = Unit.objects.create(
            module=cls.module, title='Lesson', slug='lesson', sort_order=1,
            body='Lesson body.',
        )

        cls.anon = AnonymousUser()

        cls.free_user = User.objects.create_user(email='free@test.com')
        set_membership(cls.free_user, tier=cls.free_tier)

        cls.basic_user = User.objects.create_user(email='basic@test.com')
        set_membership(cls.basic_user, tier=cls.basic_tier)

        cls.main_user = User.objects.create_user(email='main@test.com')
        set_membership(cls.main_user, tier=cls.main_tier)

        cls.premium_user = User.objects.create_user(email='premium@test.com')
        set_membership(cls.premium_user, tier=cls.premium_tier)

        cls.entitled_free_user = User.objects.create_user(email='entitled-free@test.com')
        set_membership(cls.entitled_free_user, tier=cls.free_tier)
        CourseAccess.objects.create(
            user=cls.entitled_free_user, course=cls.course, access_type='granted',
        )

        cls.entitled_premium_user = User.objects.create_user(email='entitled-premium@test.com')
        set_membership(cls.entitled_premium_user, tier=cls.premium_tier)
        CourseAccess.objects.create(
            user=cls.entitled_premium_user, course=cls.course, access_type='purchased',
        )

        cls.staff_user = User.objects.create_user(email='staff@test.com', is_staff=True)
        cls.superuser = User.objects.create_user(
            email='super@test.com', is_staff=True, is_superuser=True,
        )

    def test_anonymous_denied_course_and_unit(self):
        self.assertFalse(can_access(self.anon, self.course))
        self.assertFalse(can_access(self.anon, self.unit))

    def test_free_tier_denied(self):
        self.assertFalse(can_access(self.free_user, self.course))
        self.assertFalse(can_access(self.free_user, self.unit))

    def test_basic_tier_denied(self):
        self.assertFalse(can_access(self.basic_user, self.course))
        self.assertFalse(can_access(self.basic_user, self.unit))

    def test_main_tier_denied_critical_regression_case(self):
        """A Main subscriber does NOT get in just from their tier."""
        self.assertFalse(can_access(self.main_user, self.course))
        self.assertFalse(can_access(self.main_user, self.unit))

    def test_premium_tier_denied(self):
        """Premium (highest tier) does not bypass entitlement mode either."""
        self.assertFalse(can_access(self.premium_user, self.course))
        self.assertFalse(can_access(self.premium_user, self.unit))

    def test_entitled_free_tier_user_granted(self):
        """A CourseAccess grant opens the course at any tier, including Free."""
        self.assertTrue(can_access(self.entitled_free_user, self.course))
        self.assertTrue(can_access(self.entitled_free_user, self.unit))

    def test_entitled_premium_user_granted(self):
        self.assertTrue(can_access(self.entitled_premium_user, self.course))
        self.assertTrue(can_access(self.entitled_premium_user, self.unit))

    def test_staff_granted_unconditionally(self):
        self.assertTrue(can_access(self.staff_user, self.course))
        self.assertTrue(can_access(self.staff_user, self.unit))

    def test_superuser_granted_unconditionally(self):
        self.assertTrue(can_access(self.superuser, self.course))
        self.assertTrue(can_access(self.superuser, self.unit))

    def test_gated_reason_is_entitlement_required_not_insufficient_tier(self):
        self.assertEqual(get_gated_reason(self.main_user, self.course), 'entitlement_required')
        self.assertEqual(get_gated_reason(self.main_user, self.unit), 'entitlement_required')
        self.assertEqual(get_gated_reason(self.premium_user, self.course), 'entitlement_required')
        self.assertEqual(get_gated_reason(self.anon, self.course), 'entitlement_required')

    def test_gated_reason_empty_when_access_granted(self):
        self.assertEqual(get_gated_reason(self.entitled_free_user, self.course), '')
        self.assertEqual(get_gated_reason(self.staff_user, self.course), '')


class EntitlementNoOpAtOpenAndRegisteredLevelsTest(TierSetupMixin, TestCase):
    """access_mode='entitlement' is documented as a no-op below LEVEL_BASIC."""

    def test_entitlement_mode_no_op_at_open_level(self):
        course = _make_entitlement_course(
            required_level=LEVEL_OPEN, slug='open-entitlement',
        )
        self.assertTrue(can_access(AnonymousUser(), course))
        self.assertEqual(get_gated_reason(AnonymousUser(), course), '')

    def test_entitlement_mode_no_op_at_registered_level(self):
        course = _make_entitlement_course(
            required_level=LEVEL_REGISTERED, slug='registered-entitlement',
        )
        free_user = User.objects.create_user(email='reg-free@test.com')
        set_membership(free_user, tier=self.free_tier)
        free_user.email_verified = True
        free_user.save()
        self.assertTrue(can_access(free_user, course))
        # Anonymous is still denied at the registered wall — that denial is
        # the ordinary authentication_required reason, not entitlement.
        self.assertFalse(can_access(AnonymousUser(), course))
        self.assertEqual(
            get_gated_reason(AnonymousUser(), course), 'authentication_required',
        )


class BuildGatedAccessCopyEntitlementTest(TestCase):
    """build_gated_access_copy() 'entitlement_required' branch."""

    def test_heading_includes_program_label(self):
        copy = build_gated_access_copy(
            gated_reason='entitlement_required',
            verb='access this course',
            noun='course',
            required_level=LEVEL_MAIN,
            enroll_url='https://maven.com/x',
            program_label='Maven',
        )
        self.assertEqual(copy['gated_heading'], 'Enroll via Maven to access this course')

    def test_heading_falls_back_without_program_label(self):
        copy = build_gated_access_copy(
            gated_reason='entitlement_required',
            verb='read this lesson',
            noun='lesson',
            required_level=LEVEL_MAIN,
            enroll_url='https://maven.com/x',
            program_label='',
        )
        self.assertEqual(copy['gated_heading'], 'Enroll to read this lesson')

    def test_no_tier_pill(self):
        copy = build_gated_access_copy(
            gated_reason='entitlement_required',
            verb='access this course',
            noun='course',
            required_level=LEVEL_MAIN,
            enroll_url='https://maven.com/x',
            program_label='Maven',
        )
        self.assertEqual(copy['required_tier_name'], '')

    def test_cta_points_at_enroll_url(self):
        copy = build_gated_access_copy(
            gated_reason='entitlement_required',
            verb='access this course',
            noun='course',
            required_level=LEVEL_MAIN,
            enroll_url='https://maven.com/x',
            program_label='Maven',
        )
        self.assertEqual(copy['gated_cta_url'], 'https://maven.com/x')
        self.assertEqual(copy['gated_cta_label'], 'Enroll now')

    def test_description_is_sold_separately_copy(self):
        copy = build_gated_access_copy(
            gated_reason='entitlement_required',
            verb='access this course',
            noun='course',
            required_level=LEVEL_MAIN,
            enroll_url='https://maven.com/x',
            program_label='Maven',
        )
        self.assertEqual(
            copy['gated_description'],
            "This is an external course, run on another platform — it "
            "isn't included in any AI Shipping Labs membership plan.",
        )

    def test_blank_enroll_url_yields_blank_cta_url(self):
        copy = build_gated_access_copy(
            gated_reason='entitlement_required',
            verb='access this course',
            noun='course',
            required_level=LEVEL_MAIN,
            enroll_url=None,
            program_label='Maven',
        )
        self.assertEqual(copy['gated_cta_url'], '')


class BuildGatingContextEntitlementTest(TierSetupMixin, TestCase):
    """build_gating_context() wires enroll_url/program_label through automatically."""

    def test_gating_context_carries_enroll_url_and_program_label(self):
        course = _make_entitlement_course()
        main_user = User.objects.create_user(email='ctx-main@test.com')
        set_membership(main_user, tier=self.main_tier)

        gating = build_gating_context(main_user, course, 'course')

        self.assertTrue(gating['is_gated'])
        self.assertEqual(gating['gated_reason'], 'entitlement_required')
        self.assertTrue(gating['gated_entitlement'])
        self.assertEqual(
            gating['gated_cta_url'],
            'https://maven.com/alexey-grigorev/from-rag-to-agents',
        )
        self.assertIn('Maven', gating['gated_heading'])

    def test_gated_entitlement_false_for_tier_mode_course(self):
        course = Course.objects.create(
            title='Tier Course', slug='tier-course',
            status='published', required_level=LEVEL_MAIN,
        )
        main_user = User.objects.create_user(email='ctx-tier-main@test.com')
        set_membership(main_user, tier=self.basic_tier)

        gating = build_gating_context(main_user, course, 'course')

        self.assertEqual(gating['gated_reason'], 'insufficient_tier')
        self.assertFalse(gating.get('gated_entitlement'))


@tag('core')
class TierModeUnaffectedRegressionTest(TierSetupMixin, TestCase):
    """Every existing (tier-mode) course behaves exactly as before (#1658).

    access_mode defaults to 'tier' for every course that doesn't opt in,
    so this exercises the pre-#1658 code paths through the new branch and
    proves nothing regressed.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.course = Course.objects.create(
            title='Ordinary Paid Course', slug='ordinary-paid-course',
            status='published', required_level=LEVEL_MAIN,
        )
        cls.module = Module.objects.create(
            course=cls.course, title='Module', slug='module', sort_order=1,
        )
        cls.unit = Unit.objects.create(
            module=cls.module, title='Lesson', slug='lesson', sort_order=1,
            body='Lesson body.',
        )

    def test_access_mode_defaults_to_tier(self):
        self.assertEqual(self.course.access_mode, 'tier')
        self.assertFalse(self.course.is_entitlement_mode)

    def test_main_tier_user_granted_by_tier_alone(self):
        user = User.objects.create_user(email='regress-main@test.com')
        set_membership(user, tier=self.main_tier)
        self.assertTrue(can_access(user, self.course))
        self.assertTrue(can_access(user, self.unit))

    def test_basic_tier_user_denied_below_required_level(self):
        user = User.objects.create_user(email='regress-basic@test.com')
        set_membership(user, tier=self.basic_tier)
        self.assertFalse(can_access(user, self.course))
        self.assertEqual(get_gated_reason(user, self.course), 'insufficient_tier')

    def test_staff_granted_via_tier_shortcut(self):
        staff = User.objects.create_user(email='regress-staff@test.com', is_staff=True)
        self.assertTrue(can_access(staff, self.course))

    def test_course_access_grant_still_bypasses_tier(self):
        user = User.objects.create_user(email='regress-grant@test.com')
        set_membership(user, tier=self.free_tier)
        CourseAccess.objects.create(user=user, course=self.course, access_type='granted')
        self.assertTrue(can_access(user, self.course))
        self.assertTrue(can_access(user, self.unit))
