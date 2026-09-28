"""Enrolled learners land on course Home; everyone else on the landing.

One rule (``content.services.course_navigation``) decides where an in-app
link to a course goes. These tests pin the rule itself, the landing-page
redirect that enforces it, and the learner surfaces that link back to a
course (catalog card, dashboard, reader, drip lock, peer review pages).
"""

import datetime
import re

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import TestCase
from django.utils import timezone

from content.access import LEVEL_MAIN
from content.models import (
    Cohort,
    CohortEnrollment,
    Course,
    Enrollment,
    Module,
    Unit,
)
from content.services.course_navigation import (
    course_entry_url,
    course_home_url,
    course_overview_url,
)

User = get_user_model()


def _anchor_href(response, testid):
    match = re.search(
        r'<a href="([^"]*)"[^>]*data-testid="' + re.escape(testid) + '"',
        response.content.decode(),
    )
    assert match, f'No link with data-testid={testid}'
    return match.group(1)


class CourseEntryUrlTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(
            title='Entry course', slug='entry-course', status='published',
            required_level=0,
        )
        cls.paid_course = Course.objects.create(
            title='Paid course', slug='paid-course', status='published',
            required_level=LEVEL_MAIN,
        )
        cls.learner = User.objects.create_user(
            email='entry@example.com', password='pw', email_verified=True,
        )
        Enrollment.objects.create(user=cls.learner, course=cls.course)
        cls.visitor = User.objects.create_user(
            email='visitor@example.com', password='pw', email_verified=True,
        )

    def _fresh(self, user):
        # The rule memoizes per user instance; tests read a clean instance.
        return User.objects.get(pk=user.pk)

    def test_enrolled_learner_gets_home_with_selected_cohort(self):
        user = self._fresh(self.learner)
        self.assertEqual(
            course_entry_url(user, self.course, cohort='4'),
            '/courses/entry-course/home?cohort=4',
        )

    def test_enrolled_learner_gets_requested_home_tab(self):
        self.assertEqual(
            course_entry_url(self._fresh(self.learner), self.course, section='projects'),
            '/courses/entry-course/home/projects',
        )

    def test_anonymous_gets_landing_keeping_cohort(self):
        self.assertEqual(course_entry_url(AnonymousUser(), self.course), '/courses/entry-course')
        self.assertEqual(
            course_entry_url(AnonymousUser(), self.course, cohort='4'),
            '/courses/entry-course?cohort=4',
        )

    def test_access_without_enrollment_gets_landing(self):
        self.assertEqual(
            course_entry_url(self._fresh(self.visitor), self.course),
            '/courses/entry-course',
        )

    def test_enrolled_without_access_gets_landing(self):
        Enrollment.objects.create(user=self.visitor, course=self.paid_course)
        self.assertEqual(
            course_entry_url(self._fresh(self.visitor), self.paid_course),
            '/courses/paid-course',
        )

    def test_enrolled_course_ids_hint_replaces_enrollment_lookup(self):
        user = self._fresh(self.visitor)
        with self.assertNumQueries(0):
            url = course_entry_url(
                user, self.course, enrolled_course_ids={self.course.pk},
            )
        self.assertEqual(url, '/courses/entry-course/home')

    def test_overview_url_bypasses_redirect_and_keeps_cohort(self):
        self.assertEqual(
            course_overview_url(self.course, cohort='4'),
            '/courses/entry-course?view=overview&cohort=4',
        )
        self.assertEqual(course_home_url(self.course), '/courses/entry-course/home')


class CourseLandingRedirectTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(
            title='Redirect course', slug='redirect-course', status='published',
            required_level=0,
        )
        cls.paid_course = Course.objects.create(
            title='Paid redirect', slug='paid-redirect', status='published',
            required_level=LEVEL_MAIN,
        )
        cls.user = User.objects.create_user(
            email='redirect@example.com', password='pw', email_verified=True,
        )

    def test_enrolled_learner_redirected_to_home_keeping_cohort(self):
        Enrollment.objects.create(user=self.user, course=self.course)
        self.client.force_login(self.user)
        self.assertRedirects(
            self.client.get('/courses/redirect-course?cohort=4'),
            '/courses/redirect-course/home?cohort=4',
            fetch_redirect_response=False,
        )

    def test_view_overview_keeps_landing_for_enrolled_learner(self):
        Enrollment.objects.create(user=self.user, course=self.course)
        self.client.force_login(self.user)
        response = self.client.get('/courses/redirect-course?view=overview')
        self.assertTemplateUsed(response, 'content/course_detail.html')

    def test_anonymous_and_non_enrolled_see_landing(self):
        self.assertTemplateUsed(
            self.client.get('/courses/redirect-course'), 'content/course_detail.html',
        )
        self.client.force_login(self.user)
        self.assertTemplateUsed(
            self.client.get('/courses/redirect-course'), 'content/course_detail.html',
        )

    def test_enrolled_without_access_sees_landing(self):
        Enrollment.objects.create(user=self.user, course=self.paid_course)
        self.client.force_login(self.user)
        response = self.client.get('/courses/paid-redirect')
        self.assertTemplateUsed(response, 'content/course_detail.html')


class CourseLinkSurfacesTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(
            title='Surface course', slug='surface-course', status='published',
            required_level=0, peer_review_enabled=True,
        )
        cls.module = Module.objects.create(
            course=cls.course, title='Week 1', slug='week-1', sort_order=1,
        )
        cls.lesson = Unit.objects.create(
            module=cls.module, title='Lesson', slug='lesson', sort_order=1,
        )
        cls.locked = Unit.objects.create(
            module=cls.module, title='Later', slug='later', sort_order=2,
            available_after_days=30,
        )
        today = timezone.localdate()
        cls.cohort = Cohort.objects.create(
            course=cls.course, name='Cohort 4', external_key='4',
            start_date=today - datetime.timedelta(days=1),
            end_date=today + datetime.timedelta(days=60),
        )
        cls.learner = User.objects.create_user(
            email='surface@example.com', password='pw', email_verified=True,
        )
        Enrollment.objects.create(user=cls.learner, course=cls.course)
        CohortEnrollment.objects.create(user=cls.learner, cohort=cls.cohort)

    def test_catalog_card_opens_home_for_enrolled_and_landing_for_anonymous(self):
        anonymous = self.client.get('/courses')
        self.assertContains(anonymous, 'href="/courses/surface-course"')
        self.assertNotContains(anonymous, 'href="/courses/surface-course/home"')

        self.client.force_login(self.learner)
        enrolled = self.client.get('/courses')
        self.assertContains(enrolled, 'href="/courses/surface-course/home"')

    def test_dashboard_continue_card_opens_course_home(self):
        self.client.force_login(self.learner)
        response = self.client.get('/')
        item = next(
            item for item in response.context['in_progress_learning']
            if item['kind'] == 'course'
        )
        self.assertEqual(item['home_url'], '/courses/surface-course/home')
        self.assertContains(response, 'href="/courses/surface-course/home"')

    def test_reader_back_link_opens_home_keeping_cohort(self):
        self.client.force_login(self.learner)
        response = self.client.get(self.lesson.get_absolute_url() + '?cohort=4')
        aside = re.search(
            r'<aside id="content-sidebar-aside".*?<a href="([^"]*)"',
            response.content.decode(), re.S,
        )
        self.assertEqual(aside.group(1), '/courses/surface-course/home?cohort=4')

    def test_drip_locked_back_links_open_home(self):
        self.client.force_login(self.learner)
        response = self.client.get(self.locked.get_absolute_url() + '?cohort=4')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            _anchor_href(response, 'teaser-back-link'),
            '/courses/surface-course/home?cohort=4',
        )
        self.assertEqual(
            response.context['gated_cta_url'], '/courses/surface-course/home?cohort=4',
        )

    def test_peer_review_back_link_opens_projects_tab(self):
        self.client.force_login(self.learner)
        response = self.client.get('/courses/surface-course/submit')
        self.assertContains(response, 'href="/courses/surface-course/home/projects"')

    def test_peer_review_back_link_is_landing_without_enrollment(self):
        visitor = User.objects.create_user(
            email='pr-visitor@example.com', password='pw', email_verified=True,
        )
        self.client.force_login(visitor)
        response = self.client.get('/courses/surface-course/reviews')
        self.assertContains(response, 'href="/courses/surface-course"')
        self.assertNotContains(response, 'href="/courses/surface-course/home')
