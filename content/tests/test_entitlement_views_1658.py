"""View/template tests for entitlement-mode courses (issue #1658).

Covers:
- Course detail page: "Sold separately" badge, enroll CTA (not tier
  pricing), gated unit teaser, hidden cohort enroll/unenroll buttons +
  "Enrolled" badge
- /courses catalog: "Sold separately" section, tag-filter isolation,
  empty-state hiding when no entitlement course is published
- Cohort self-enroll/unenroll API: unconditional 403 refusal
- Regression: tier-mode course/catalog rendering is unaffected
"""

import datetime
import json

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from content.access import LEVEL_MAIN
from content.models import Cohort, CohortEnrollment, Course, CourseAccess, Module, Unit
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


@tag('core')
class CourseDetailEntitlementBadgeTest(TierSetupMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.course = _make_entitlement_course()
        cls.tier_course = Course.objects.create(
            title='Tier Course', slug='tier-course',
            status='published', required_level=LEVEL_MAIN,
        )

    def test_sold_separately_badge_shown_for_entitlement_course(self):
        response = self.client.get('/courses/ai-engineering-buildcamp')
        self.assertContains(response, 'Sold separately')

    def test_no_sold_separately_badge_for_tier_course(self):
        response = self.client.get('/courses/tier-course')
        self.assertNotContains(response, 'Sold separately')


@tag('core')
class CourseDetailEntitlementCtaTest(TierSetupMixin, TestCase):
    """Main/Premium subscribers see the enroll CTA, not tier pricing."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.course = _make_entitlement_course()

    def _login(self, email, tier):
        user = User.objects.create_user(email=email, password='testpass')
        set_membership(user, tier=tier)
        self.client.login(email=email, password='testpass')
        return user

    def test_anonymous_sees_enroll_cta_not_membership_link(self):
        response = self.client.get('/courses/ai-engineering-buildcamp')
        self.assertContains(response, 'Enroll via Maven')
        self.assertContains(
            response, 'https://maven.com/alexey-grigorev/from-rag-to-agents',
        )
        self.assertNotContains(response, 'Unlock with')

    def test_enroll_cta_opens_in_new_tab(self):
        response = self.client.get('/courses/ai-engineering-buildcamp')
        body = response.content.decode()
        cta_index = body.index('course-gated-cta-button')
        # The anchor tag containing the testid also carries target="_blank".
        anchor_start = body.rindex('<a', 0, cta_index)
        anchor_tag = body[anchor_start:cta_index]
        self.assertIn('target="_blank"', anchor_tag)
        self.assertIn('rel="noopener noreferrer"', anchor_tag)

    def test_main_tier_subscriber_denied_and_sees_enroll_cta(self):
        self._login('main@test.com', self.main_tier)
        response = self.client.get('/courses/ai-engineering-buildcamp')
        self.assertContains(response, 'Enroll via Maven')
        body = response.content.decode()
        cta_index = body.index('course-gated-cta-button')
        anchor_start = body.rindex('<a', 0, cta_index)
        anchor_tag = body[anchor_start:cta_index]
        self.assertNotIn('/membership', anchor_tag)
        self.assertIn(
            'https://maven.com/alexey-grigorev/from-rag-to-agents', anchor_tag,
        )

    def test_premium_tier_subscriber_denied_and_sees_enroll_cta(self):
        self._login('premium@test.com', self.premium_tier)
        response = self.client.get('/courses/ai-engineering-buildcamp')
        self.assertContains(response, 'Enroll via Maven')

    def test_entitled_free_member_sees_no_gated_banner(self):
        user = self._login('entitled@test.com', self.free_tier)
        CourseAccess.objects.create(user=user, course=self.course, access_type='granted')
        response = self.client.get('/courses/ai-engineering-buildcamp')
        self.assertNotContains(response, 'Enroll via Maven')
        self.assertNotContains(response, 'course-gated-cta')

    def test_staff_sees_no_gated_banner_without_grant(self):
        User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')
        response = self.client.get('/courses/ai-engineering-buildcamp')
        self.assertNotContains(response, 'Enroll via Maven')

    def test_blank_enroll_url_hides_cta_button_but_shows_heading(self):
        course = _make_entitlement_course(
            slug='no-enroll-url', enroll_url='', program_label='Maven',
        )
        response = self.client.get(f'/courses/{course.slug}')
        self.assertContains(response, 'Enroll via Maven')
        self.assertNotContains(response, 'data-testid="course-gated-cta-button"')


@tag('core')
class CourseUnitEntitlementTeaserTest(TierSetupMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.course = _make_entitlement_course()
        cls.module = Module.objects.create(
            course=cls.course, title='Module', slug='module', sort_order=1,
        )
        cls.unit = Unit.objects.create(
            module=cls.module, title='Lesson', slug='lesson', sort_order=1,
            body='Lesson body content that is long enough to tease out.',
        )

    def test_main_tier_user_sees_enroll_teaser_not_upgrade_copy(self):
        user = User.objects.create_user(email='main-unit@test.com', password='testpass')
        set_membership(user, tier=self.main_tier)
        self.client.login(email='main-unit@test.com', password='testpass')

        response = self.client.get('/courses/ai-engineering-buildcamp/module/lesson')

        self.assertEqual(response.status_code, 403)
        self.assertContains(response, 'Enroll via Maven to read this lesson', status_code=403)
        self.assertNotContains(response, 'Upgrade to Main', status_code=403)

    def test_entitled_user_sees_full_lesson(self):
        user = User.objects.create_user(email='entitled-unit@test.com', password='testpass')
        set_membership(user, tier=self.free_tier)
        CourseAccess.objects.create(user=user, course=self.course, access_type='granted')
        self.client.login(email='entitled-unit@test.com', password='testpass')

        response = self.client.get('/courses/ai-engineering-buildcamp/module/lesson')

        self.assertContains(response, 'Lesson body content')

    def test_staff_sees_full_lesson_without_grant(self):
        User.objects.create_user(
            email='staff-unit@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff-unit@test.com', password='testpass')

        response = self.client.get('/courses/ai-engineering-buildcamp/module/lesson')

        self.assertContains(response, 'Lesson body content')


@tag('core')
class CourseDetailEntitlementCohortTest(TierSetupMixin, TestCase):
    """Active Cohorts block hides self-enroll buttons for entitlement courses."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.course = _make_entitlement_course()
        cls.cohort = Cohort.objects.create(
            course=cls.course, name='Cohort 4',
            start_date=datetime.date(2026, 9, 21),
            end_date=datetime.date(2026, 12, 1),
            is_active=True,
        )

    def test_no_enroll_button_for_entitled_member(self):
        user = User.objects.create_user(email='entitled-cohort@test.com', password='testpass')
        set_membership(user, tier=self.free_tier)
        CourseAccess.objects.create(user=user, course=self.course, access_type='granted')
        self.client.login(email='entitled-cohort@test.com', password='testpass')

        response = self.client.get('/courses/ai-engineering-buildcamp')

        self.assertContains(response, 'Cohort 4')
        self.assertNotContains(response, 'data-action="enroll"')
        self.assertNotContains(response, 'data-action="unenroll"')

    def test_no_enroll_button_for_staff(self):
        User.objects.create_user(
            email='staff-cohort@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff-cohort@test.com', password='testpass')

        response = self.client.get('/courses/ai-engineering-buildcamp')

        self.assertNotContains(response, 'data-action="enroll"')

    def test_enrolled_badge_shown_for_already_enrolled_user(self):
        user = User.objects.create_user(email='enrolled-cohort@test.com', password='testpass')
        set_membership(user, tier=self.free_tier)
        CourseAccess.objects.create(user=user, course=self.course, access_type='granted')
        CohortEnrollment.objects.create(cohort=self.cohort, user=user)
        self.client.login(email='enrolled-cohort@test.com', password='testpass')

        response = self.client.get('/courses/ai-engineering-buildcamp')

        self.assertContains(response, 'data-testid="cohort-enrolled-badge"')

    def test_no_badge_for_entitled_user_not_enrolled_in_cohort(self):
        user = User.objects.create_user(email='not-enrolled-cohort@test.com', password='testpass')
        set_membership(user, tier=self.free_tier)
        CourseAccess.objects.create(user=user, course=self.course, access_type='granted')
        self.client.login(email='not-enrolled-cohort@test.com', password='testpass')

        response = self.client.get('/courses/ai-engineering-buildcamp')

        self.assertNotContains(response, 'data-testid="cohort-enrolled-badge"')


@tag('core')
class CohortSelfEnrollApiRefusalTest(TierSetupMixin, TestCase):
    """POST /api/courses/{slug}/cohorts/{id}/enroll|unenroll — 403 for entitlement courses."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.course = _make_entitlement_course()
        cls.cohort = Cohort.objects.create(
            course=cls.course, name='Cohort 4',
            start_date=datetime.date(2026, 9, 21),
            end_date=datetime.date(2026, 12, 1),
            is_active=True,
        )

    def _enroll_url(self):
        return f'/api/courses/{self.course.slug}/cohorts/{self.cohort.pk}/enroll'

    def _unenroll_url(self):
        return f'/api/courses/{self.course.slug}/cohorts/{self.cohort.pk}/unenroll'

    def test_enroll_refused_for_plain_member(self):
        user = User.objects.create_user(email='plain@test.com', password='testpass')
        set_membership(user, tier=self.main_tier)
        self.client.login(email='plain@test.com', password='testpass')

        response = self.client.post(self._enroll_url())

        self.assertEqual(response.status_code, 403)
        data = json.loads(response.content)
        self.assertIn('staff', data['error'].lower())
        self.assertFalse(
            CohortEnrollment.objects.filter(cohort=self.cohort, user=user).exists()
        )

    def test_enroll_refused_for_entitled_user(self):
        """Even a user who already holds CourseAccess cannot self-enroll."""
        user = User.objects.create_user(email='entitled-api@test.com', password='testpass')
        set_membership(user, tier=self.free_tier)
        CourseAccess.objects.create(user=user, course=self.course, access_type='granted')
        self.client.login(email='entitled-api@test.com', password='testpass')

        response = self.client.post(self._enroll_url())

        self.assertEqual(response.status_code, 403)

    def test_enroll_refused_for_staff(self):
        User.objects.create_user(
            email='staff-api@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff-api@test.com', password='testpass')

        response = self.client.post(self._enroll_url())

        self.assertEqual(response.status_code, 403)

    def test_unenroll_refused_for_entitled_user(self):
        user = User.objects.create_user(email='unenroll-api@test.com', password='testpass')
        set_membership(user, tier=self.free_tier)
        CourseAccess.objects.create(user=user, course=self.course, access_type='granted')
        CohortEnrollment.objects.create(cohort=self.cohort, user=user)
        self.client.login(email='unenroll-api@test.com', password='testpass')

        response = self.client.post(self._unenroll_url())

        self.assertEqual(response.status_code, 403)
        # The user must remain enrolled — no path back out via this endpoint.
        self.assertTrue(
            CohortEnrollment.objects.filter(cohort=self.cohort, user=user).exists()
        )

    def test_tier_mode_course_enroll_unaffected(self):
        """Regression: a plain tier-mode cohort enroll still succeeds."""
        tier_course = Course.objects.create(
            title='Tier Cohort Course', slug='tier-cohort-course',
            status='published', required_level=LEVEL_MAIN,
        )
        tier_cohort = Cohort.objects.create(
            course=tier_course, name='T1',
            start_date=datetime.date(2026, 9, 21),
            end_date=datetime.date(2026, 12, 1),
            is_active=True,
        )
        user = User.objects.create_user(email='tier-enroll@test.com', password='testpass')
        set_membership(user, tier=self.main_tier)
        self.client.login(email='tier-enroll@test.com', password='testpass')

        response = self.client.post(
            f'/api/courses/{tier_course.slug}/cohorts/{tier_cohort.pk}/enroll',
        )

        data = json.loads(response.content)
        self.assertTrue(data['enrolled'])
        self.assertTrue(
            CohortEnrollment.objects.filter(cohort=tier_cohort, user=user).exists()
        )


@tag('core')
class CoursesListEntitlementSectionTest(TierSetupMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.standard1 = Course.objects.create(
            title='Standard Course One', slug='standard-course-one',
            status='published', tags=['python'],
        )
        cls.standard2 = Course.objects.create(
            title='Standard Course Two', slug='standard-course-two',
            status='published', tags=['rag'],
        )
        cls.entitlement = _make_entitlement_course(tags=['maven-exclusive'])

    def test_sold_separately_section_renders_below_standard_grid(self):
        response = self.client.get('/courses')
        self.assertContains(response, 'Sold separately')
        self.assertContains(response, 'AI Engineering Buildcamp')
        self.assertContains(
            response,
            'Independent programs with their own enrollment',
        )

    def test_standard_grid_unaffected_by_entitlement_course(self):
        response = self.client.get('/courses')
        self.assertContains(response, 'Standard Course One')
        self.assertContains(response, 'Standard Course Two')

    def test_entitlement_course_excluded_from_tag_facet_pool(self):
        response = self.client.get('/courses')
        # all_tags drives the tag-filter facet pool; the entitlement
        # course's tag must not feed it, only the standard courses' tags.
        self.assertEqual(
            set(response.context['all_tags']), {'python', 'rag'},
        )

    def test_tag_filter_leaves_sold_separately_section_untouched(self):
        response = self.client.get('/courses?tag=nonexistent-tag')
        self.assertContains(response, 'No courses found with the selected tags')
        # Entitlement section still renders, unaffected by the filter.
        self.assertContains(response, 'Sold separately')
        self.assertContains(response, 'AI Engineering Buildcamp')

    def test_tag_filter_on_standard_tag_does_not_remove_entitlement_section(self):
        response = self.client.get('/courses?tag=python')
        self.assertContains(response, 'Standard Course One')
        self.assertNotContains(response, 'Standard Course Two')
        self.assertContains(response, 'AI Engineering Buildcamp')

    def test_entitlement_course_shows_sold_separately_badge_on_card(self):
        response = self.client.get('/courses')
        self.assertContains(response, 'data-testid="course-sold-separately-badge"')


class CoursesListNoEntitlementCoursesTest(TestCase):
    """Zero entitlement-mode courses published: section is hidden entirely."""

    @classmethod
    def setUpTestData(cls):
        cls.standard = Course.objects.create(
            title='Only Standard Course', slug='only-standard-course',
            status='published',
        )

    def test_no_sold_separately_heading(self):
        response = self.client.get('/courses')
        self.assertNotContains(response, 'Sold separately')

    def test_no_sold_separately_section_container(self):
        response = self.client.get('/courses')
        self.assertNotContains(response, 'data-testid="sold-separately-section"')

    def test_standard_catalog_renders_normally(self):
        response = self.client.get('/courses')
        self.assertContains(response, 'Only Standard Course')
