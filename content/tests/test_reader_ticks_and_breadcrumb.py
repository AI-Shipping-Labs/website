"""Reader sidebar completion ticks and course breadcrumb destination.

Ticks: a completed unit shows the completion tick in the reader sidebar
wherever it is listed, including the currently selected row, in a leaf
top-level module (the Buildcamp "Course Logistics" shape) and on module
overview pages whose sidebar lists sibling submodules.

Breadcrumb: no ``Courses`` catalog crumb; the course crumb leads enrolled
learners to course Home (keeping ``?cohort=``) and everyone else to the
public landing page.
"""

import datetime
import re

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from content.models import (
    Cohort,
    CohortEnrollment,
    Course,
    Enrollment,
    Module,
    Unit,
    UserCourseProgress,
)

CHECK_MARKER = 'data-lucide="check-circle-2"'


def _sidebar(response):
    match = re.search(
        r'<nav\b[^>]*\bid="sidebar-nav"[^>]*>.*?</nav>',
        response.content.decode(), re.S,
    )
    assert match, 'Reader sidebar navigation is missing'
    return match.group()


def _row(html, href):
    """Return the sidebar row anchor whose href starts with ``href``."""
    match = re.search(
        r'<a href="' + re.escape(href) + r'(?:\?[^"]*)?"[^>]*>.*?</a>',
        html, re.S,
    )
    assert match, f'No sidebar row for {href}'
    return match.group()


def _breadcrumb(response):
    match = re.search(
        r'<div\b[^>]*data-reader-breadcrumb[^>]*>.*?</div>',
        response.content.decode(), re.S,
    )
    assert match, 'Reader breadcrumb is missing'
    return match.group()


class ReaderSidebarCompletionTickTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            email='ticks@example.com', password='pw', email_verified=True,
        )
        cls.course = Course.objects.create(
            title='Tick course', slug='tick-course', status='published',
            required_level=0, reader_navigation_scope='module',
        )
        # Leaf top-level module: units hang directly off it, no children.
        cls.logistics = Module.objects.create(
            course=cls.course, title='Logistics', slug='logistics', sort_order=0,
        )
        cls.log_a = Unit.objects.create(
            module=cls.logistics, title='Overview', slug='overview', sort_order=1,
        )
        cls.log_b = Unit.objects.create(
            module=cls.logistics, title='Communication', slug='communication',
            sort_order=2,
        )
        cls.log_c = Unit.objects.create(
            module=cls.logistics, title='Office hours', slug='office-hours',
            sort_order=3,
        )
        week1 = Module.objects.create(
            course=cls.course, title='Week 1', slug='week-1', sort_order=1,
        )
        cls.first = Module.objects.create(
            course=cls.course, parent=week1, title='First topic', slug='first',
            sort_order=1,
        )
        cls.second = Module.objects.create(
            course=cls.course, parent=week1, title='Second topic', slug='second',
            sort_order=2,
        )
        cls.first_lesson = Unit.objects.create(
            module=cls.first, title='First lesson', slug='lesson', sort_order=1,
        )
        cls.second_lesson = Unit.objects.create(
            module=cls.second, title='Second lesson', slug='lesson', sort_order=1,
        )
        now = timezone.now()
        for unit in (cls.log_a, cls.log_b, cls.first_lesson, cls.second_lesson):
            UserCourseProgress.objects.create(
                user=cls.user, unit=unit, completed_at=now,
            )

    def setUp(self):
        self.client.force_login(self.user)

    def test_leaf_module_completed_rows_show_tick_including_current(self):
        response = self.client.get(self.log_b.get_absolute_url())
        sidebar = _sidebar(response)
        self.assertIn('data-testid="reader-scoped-module"', sidebar)
        other_completed = _row(sidebar, self.log_a.get_absolute_url())
        current_completed = _row(sidebar, self.log_b.get_absolute_url())
        incomplete = _row(sidebar, self.log_c.get_absolute_url())
        self.assertIn(CHECK_MARKER, other_completed)
        self.assertIn('aria-current="page"', current_completed)
        self.assertIn(CHECK_MARKER, current_completed)
        self.assertNotIn(CHECK_MARKER, incomplete)

    def test_current_incomplete_row_keeps_type_icon(self):
        response = self.client.get(self.log_c.get_absolute_url())
        current = _row(_sidebar(response), self.log_c.get_absolute_url())
        self.assertIn('aria-current="page"', current)
        self.assertNotIn(CHECK_MARKER, current)
        self.assertIn('data-lucide="file-text"', current)

    def test_submodule_current_completed_row_shows_tick(self):
        response = self.client.get(self.first_lesson.get_absolute_url())
        current = _row(_sidebar(response), self.first_lesson.get_absolute_url())
        self.assertIn('aria-current="page"', current)
        self.assertIn(CHECK_MARKER, current)

    def test_submodule_overview_ticks_units_in_sibling_submodules(self):
        # The sidebar lists the whole week; completion must not be limited
        # to the overview's own module tree.
        response = self.client.get('/courses/tick-course/week-1/first')
        self.assertEqual(response.status_code, 200)
        sibling = _row(_sidebar(response), self.second_lesson.get_absolute_url())
        self.assertIn(CHECK_MARKER, sibling)


class ReaderBreadcrumbCourseCrumbTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            email='crumb@example.com', password='pw', email_verified=True,
        )
        cls.course = Course.objects.create(
            title='Crumb course', slug='crumb-course', status='published',
            required_level=0, reader_navigation_scope='module',
        )
        cls.module = Module.objects.create(
            course=cls.course, title='Week 1', slug='week-1', sort_order=1,
        )
        cls.unit = Unit.objects.create(
            module=cls.module, title='Lesson', slug='lesson', sort_order=1,
        )
        today = timezone.localdate()
        cls.cohort = Cohort.objects.create(
            course=cls.course, name='Cohort 4', external_key='4',
            start_date=today - datetime.timedelta(days=1),
            end_date=today + datetime.timedelta(days=60),
        )

    def setUp(self):
        self.client.force_login(self.user)

    def _enroll(self):
        Enrollment.objects.create(user=self.user, course=self.course)
        CohortEnrollment.objects.create(user=self.user, cohort=self.cohort)

    def test_enrolled_unit_crumb_links_to_home_with_cohort(self):
        self._enroll()
        crumb = _breadcrumb(self.client.get(self.unit.get_absolute_url() + '?cohort=4'))
        self.assertIn('href="/courses/crumb-course/home?cohort=4"', crumb)
        self.assertIn('>Crumb course</a>', crumb)
        self.assertNotIn('href="/courses"', crumb)

    def test_enrolled_module_crumb_links_to_home_with_cohort(self):
        self._enroll()
        response = self.client.get('/courses/crumb-course/week-1?cohort=4')
        self.assertEqual(response.status_code, 200)
        crumb = _breadcrumb(response)
        self.assertIn('href="/courses/crumb-course/home?cohort=4"', crumb)
        self.assertNotIn('>Courses<', crumb)

    def test_not_enrolled_crumb_links_to_landing(self):
        crumb = _breadcrumb(self.client.get(self.unit.get_absolute_url()))
        self.assertIn('href="/courses/crumb-course"', crumb)
        self.assertNotIn('/home', crumb)
        self.assertNotIn('href="/courses"', crumb)
