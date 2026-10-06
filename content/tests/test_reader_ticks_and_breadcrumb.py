"""Reader sidebar completion ticks and course breadcrumb destination.

Ticks: a completed unit shows the completion tick in the reader sidebar
wherever it is listed, including the currently selected row, in a leaf
top-level module (the Buildcamp "Course Logistics" shape) and on module
overview pages whose sidebar lists sibling submodules.

Breadcrumb: no ``Courses`` catalog crumb; the crumb starts at the
personalized course destination labelled ``My learning`` (keeping
``?cohort=``) for enrolled learners and at the public landing for
everyone else, then names the parent module for nested units and marks
the current lesson with ``aria-current="page"`` (issue #1793).
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
        r'<nav\b[^>]*data-reader-breadcrumb[^>]*>.*?</nav>',
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
        self.assertIn('>My learning</a>', crumb)
        self.assertNotIn('Crumb course', crumb)
        self.assertNotIn('href="/courses"', crumb)

    def test_enrolled_module_crumb_links_to_home_with_cohort(self):
        self._enroll()
        response = self.client.get('/courses/crumb-course/week-1?cohort=4')
        crumb = _breadcrumb(response)
        self.assertIn('href="/courses/crumb-course/home?cohort=4"', crumb)
        self.assertNotIn('>Courses<', crumb)

    def test_not_enrolled_crumb_links_to_landing(self):
        crumb = _breadcrumb(self.client.get(self.unit.get_absolute_url()))
        self.assertIn('href="/courses/crumb-course"', crumb)
        self.assertNotIn('/home', crumb)
        self.assertNotIn('href="/courses"', crumb)


class ReaderBreadcrumbLandmarkTest(TestCase):
    """The breadcrumb is a named nav landmark ending in the current
    lesson/module crumb (issue #1793)."""

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            email='landmark@example.com', password='pw', email_verified=True,
        )
        cls.course = Course.objects.create(
            title='Landmark course', slug='landmark-course', status='published',
            required_level=0, reader_navigation_scope='module',
        )
        # Leaf top-level module: the short "Course Logistics" shape —
        # the breadcrumb must be `My learning > current lesson`.
        cls.logistics = Module.objects.create(
            course=cls.course, title='Logistics', slug='logistics', sort_order=0,
        )
        cls.logistics_unit = Unit.objects.create(
            module=cls.logistics, title='Communication', slug='communication',
            sort_order=1,
        )
        # Nested unit: a meaningful parent-module crumb sits between.
        cls.week1 = Module.objects.create(
            course=cls.course, title='Week 1', slug='week-1', sort_order=1,
        )
        cls.topic = Module.objects.create(
            course=cls.course, parent=cls.week1, title='Foundations',
            slug='foundations', sort_order=1,
        )
        cls.nested_unit = Unit.objects.create(
            module=cls.topic, title='Capstone', slug='capstone', sort_order=1,
        )

    def setUp(self):
        self.client.force_login(self.user)

    def test_breadcrumb_is_named_nav_landmark(self):
        response = self.client.get(self.logistics_unit.get_absolute_url())
        self.assertContains(response, '<nav class="mb-2')
        self.assertContains(response, 'aria-label="Breadcrumb"')

    def test_leaf_unit_shows_my_learning_then_current_lesson(self):
        crumb = _breadcrumb(self.client.get(self.logistics_unit.get_absolute_url()))
        self.assertIn('>My learning</a>', crumb)
        self.assertNotIn('breadcrumb-parent-module', crumb)
        self.assertIn('aria-current="page"', crumb)
        self.assertIn('>Communication</span>', crumb)
        # No catalog crumb and no course-title crumb.
        self.assertNotIn('>Courses<', crumb)
        self.assertNotIn('Landmark course', crumb)

    def test_nested_unit_shows_parent_module_then_current_lesson(self):
        crumb = _breadcrumb(self.client.get(self.nested_unit.get_absolute_url()))
        self.assertIn('data-testid="breadcrumb-parent-module"', crumb)
        self.assertIn('>Week 1</a>', crumb)
        self.assertIn('aria-current="page"', crumb)
        self.assertIn('>Capstone</span>', crumb)

    def test_module_overview_marks_current_module(self):
        crumb = _breadcrumb(self.client.get('/courses/landmark-course/week-1'))
        self.assertIn('>My learning</a>', crumb)
        self.assertIn('aria-current="page"', crumb)
        self.assertIn('data-testid="breadcrumb-current-module"', crumb)
        self.assertIn('>Week 1</span>', crumb)
