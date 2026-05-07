"""View-level tests for the registered-wall gate (issue #465).

Covers:

- Anonymous on a registered-walled unit -> "Sign in to read" CTA, no
  upgrade-to-Basic copy
- Anonymous on a unit with ``required_level=LEVEL_OPEN`` override sees
  the lesson body
- Free verified user on a registered-walled unit reads the body
- Course detail page CTAs still reflect ``Course.required_level`` even
  when ``default_unit_required_level`` is set differently (decoupling)
- ``api_course_unit_detail`` returns 401 for anonymous + 200 for free
  verified on a registered-walled unit
"""

import json

from django.test import Client, TestCase, tag

from accounts.models import User
from content.access import (
    LEVEL_BASIC,
    LEVEL_MAIN,
    LEVEL_OPEN,
    LEVEL_REGISTERED,
)
from content.models import Course, Module, Unit
from tests.fixtures import TierSetupMixin


@tag('core')
class RegisteredCourseUnitViewTest(TierSetupMixin, TestCase):
    """Detail view + API behaviour for a registered-walled course."""

    def setUp(self):
        self.client = Client()
        self.course = Course.objects.create(
            title='Reg Walled', slug='reg-walled',
            status='published', required_level=LEVEL_OPEN,
            default_unit_required_level=LEVEL_REGISTERED,
        )
        self.module = Module.objects.create(
            course=self.course, title='Module', slug='module', sort_order=1,
        )
        self.unit = Unit.objects.create(
            module=self.module, title='Lesson 1', slug='lesson-1',
            body='# Lesson body\nFull body text here.',
            sort_order=1,
        )
        # Override unit: anonymous-readable inside the same course.
        self.open_unit = Unit.objects.create(
            module=self.module, title='Free intro', slug='free-intro',
            body='# Free intro\nAnyone can read this.',
            sort_order=2, required_level=LEVEL_OPEN,
        )

    def _login_free_verified(self):
        user = User.objects.create_user(
            email='free-v@reg.test', password='pw', email_verified=True,
        )
        user.tier = self.free_tier
        user.save()
        self.client.login(email='free-v@reg.test', password='pw')
        return user

    def test_anonymous_blocked_with_sign_in_cta(self):
        response = self.client.get('/courses/reg-walled/module/lesson-1')
        self.assertEqual(response.status_code, 403)
        # The gated card heading is the registered-wall CTA copy.
        self.assertContains(
            response, 'Sign in to read this lesson', status_code=403,
        )
        # Must NOT show any "Upgrade to <tier>" copy — that's the paid
        # CTA path.
        self.assertNotContains(
            response, 'Upgrade to Basic', status_code=403,
        )
        self.assertNotContains(
            response, 'Upgrade to Main', status_code=403,
        )
        self.assertNotContains(
            response, 'Upgrade to Premium', status_code=403,
        )
        # Sign-in URL preserves the next= parameter so post-login lands
        # back on the lesson.
        self.assertContains(
            response,
            '/accounts/login/?next=%2Fcourses%2Freg-walled%2Fmodule%2Flesson-1',
            status_code=403,
        )
        # And the secondary "Create a free account" CTA points at signup
        # with the same next= param.
        self.assertContains(
            response, 'Create a free account', status_code=403,
        )

    def test_anonymous_sees_open_override_unit(self):
        response = self.client.get('/courses/reg-walled/module/free-intro')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Anyone can read this.')
        self.assertNotContains(response, 'Sign in to read this lesson')

    def test_free_verified_user_reads_body(self):
        self._login_free_verified()
        response = self.client.get('/courses/reg-walled/module/lesson-1')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Full body text here.')

    def test_api_anonymous_returns_401(self):
        # Acceptance criterion: api_course_unit_detail returns 401 (not
        # 403) for anonymous on a registered-level unit.
        response = self.client.get(
            f'/api/courses/reg-walled/units/{self.unit.pk}',
        )
        self.assertEqual(response.status_code, 401)

    def test_api_free_verified_returns_200(self):
        self._login_free_verified()
        response = self.client.get(
            f'/api/courses/reg-walled/units/{self.unit.pk}',
        )
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(data['title'], 'Lesson 1')


@tag('core')
class CatalogVsUnitDecouplingTest(TierSetupMixin, TestCase):
    """``Course.required_level`` drives catalog/detail; units gate independently.

    Course is Basic-tier in the catalog (``required_level=LEVEL_BASIC``)
    but the lessons are open to any logged-in user
    (``default_unit_required_level=LEVEL_REGISTERED``). A free user can
    read the lessons but the catalog still markets the course as Basic.
    """

    def setUp(self):
        self.client = Client()
        self.course = Course.objects.create(
            title='Decoupled', slug='decoupled',
            status='published', required_level=LEVEL_BASIC,
            default_unit_required_level=LEVEL_REGISTERED,
        )
        self.module = Module.objects.create(
            course=self.course, title='M', slug='m', sort_order=1,
        )
        self.unit = Unit.objects.create(
            module=self.module, title='Decoupled Lesson', slug='lesson',
            body='# Body\nDecoupled body.', sort_order=1,
        )

    def _login_free_verified(self):
        user = User.objects.create_user(
            email='free-deco@reg.test', password='pw', email_verified=True,
        )
        user.tier = self.free_tier
        user.save()
        self.client.login(email='free-deco@reg.test', password='pw')
        return user

    def test_course_detail_shows_basic_cta_for_free_user(self):
        self._login_free_verified()
        response = self.client.get('/courses/decoupled')
        self.assertEqual(response.status_code, 200)
        # course.required_level is BASIC, so the detail page CTA is
        # driven by that — not by the unit default.
        self.assertContains(response, 'Unlock with Basic')

    def test_unit_body_renders_for_free_user(self):
        self._login_free_verified()
        response = self.client.get('/courses/decoupled/m/lesson')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Decoupled body.')


@tag('core')
class LegacyIsPreviewStillWorksTest(TierSetupMixin, TestCase):
    """A pre-#465 course with ``is_preview`` units stays untouched.

    ``Course.default_unit_required_level`` and ``Unit.required_level``
    stay NULL — backward compat acceptance criterion.
    """

    def setUp(self):
        self.client = Client()
        self.course = Course.objects.create(
            title='Legacy', slug='legacy',
            status='published', required_level=LEVEL_BASIC,
        )
        self.module = Module.objects.create(
            course=self.course, title='M', slug='m', sort_order=1,
        )
        self.preview = Unit.objects.create(
            module=self.module, title='Preview', slug='preview',
            body='# Preview\nLegacy preview body.',
            sort_order=1, is_preview=True,
        )
        self.gated = Unit.objects.create(
            module=self.module, title='Gated', slug='gated',
            body='# Gated\nLegacy gated body.',
            sort_order=2,
        )

    def test_columns_default_to_null(self):
        # Sanity: existing-style course rows leave both new fields null.
        self.assertIsNone(self.course.default_unit_required_level)
        self.assertIsNone(self.preview.required_level)
        self.assertIsNone(self.gated.required_level)

    def test_anonymous_reads_preview_unit(self):
        response = self.client.get('/courses/legacy/m/preview')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Legacy preview body.')

    def test_anonymous_blocked_from_gated_unit_with_signin_cta(self):
        # Pre-#465 behavior for an anonymous user on a paid course:
        # "Sign in to access this lesson" (the existing copy), not the
        # new "Sign in to read this lesson" registered-wall CTA.
        response = self.client.get('/courses/legacy/m/gated')
        self.assertEqual(response.status_code, 403)
        self.assertContains(
            response, 'Sign in to access this lesson', status_code=403,
        )
        # Critically, must NOT use the new registered-wall copy.
        self.assertNotContains(
            response, 'Sign in to read this lesson', status_code=403,
        )

    def test_basic_user_reads_gated_unit(self):
        user = User.objects.create_user(
            email='basic-legacy@reg.test', password='pw',
            email_verified=True,
        )
        user.tier = self.basic_tier
        user.save()
        self.client.login(email='basic-legacy@reg.test', password='pw')
        response = self.client.get('/courses/legacy/m/gated')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Legacy gated body.')


@tag('core')
class UnverifiedFreeOnRegisteredUnitTest(TierSetupMixin, TestCase):
    """Free + unverified user on a registered-walled unit hits verify gate."""

    def test_renders_verify_email_partial(self):
        course = Course.objects.create(
            title='Reg Verify', slug='reg-verify',
            status='published', required_level=LEVEL_OPEN,
            default_unit_required_level=LEVEL_REGISTERED,
        )
        module = Module.objects.create(
            course=course, title='M', slug='m', sort_order=1,
        )
        Unit.objects.create(
            module=module, title='L', slug='l',
            body='# Body\nVerify needed body.', sort_order=1,
        )

        user = User.objects.create_user(
            email='unv-reg@reg.test', password='pw', email_verified=False,
        )
        user.tier = self.free_tier
        user.save()
        self.client.login(email='unv-reg@reg.test', password='pw')

        response = self.client.get('/courses/reg-verify/m/l')
        # Verify gate renders 200 (existing behaviour for unverified users).
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, 'data-testid="verify-email-required-card"',
        )
        # The unauthenticated "Sign in" CTA must not show — verify-email
        # gate is a separate render path.
        self.assertNotContains(response, 'Sign in to read this lesson')


@tag('core')
class FreeIntroOnPaidCourseStillWorksTest(TierSetupMixin, TestCase):
    """Per-unit ``access: open`` on a paid course unlocks just that unit."""

    def test_open_override_renders_for_anonymous_on_paid_course(self):
        course = Course.objects.create(
            title='Paid With Intro', slug='paid-with-intro',
            status='published', required_level=LEVEL_MAIN,
        )
        module = Module.objects.create(
            course=course, title='M', slug='m', sort_order=1,
        )
        Unit.objects.create(
            module=module, title='Intro', slug='intro',
            body='# Intro\nFree intro body.',
            sort_order=1, required_level=LEVEL_OPEN,
        )
        Unit.objects.create(
            module=module, title='Paid', slug='paid',
            body='# Paid\nPaid body.',
            sort_order=2,
        )
        # Anonymous: intro readable, paid unit gated.
        intro_resp = self.client.get('/courses/paid-with-intro/m/intro')
        self.assertEqual(intro_resp.status_code, 200)
        self.assertContains(intro_resp, 'Free intro body.')

        paid_resp = self.client.get('/courses/paid-with-intro/m/paid')
        self.assertEqual(paid_resp.status_code, 403)
        self.assertContains(
            paid_resp, 'Sign in to access this lesson', status_code=403,
        )
