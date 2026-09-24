"""Tests for course mobile responsive fixes - issue #175.

Covers:
- Course detail page: syllabus module rows have truncation classes
- Course detail page: cohort enrollment uses flex-col on mobile
- Course unit detail: breadcrumb has overflow-hidden and truncate classes
- Course unit detail: top prev/next uses truncate and max-w-[40vw]
- Course unit detail: bottom prev/next buttons use truncate
- Course unit detail: sidebar has mobile toggle button
- Course unit detail: Q&A reply indentation uses ml-4 sm:ml-8
"""

import uuid

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.utils import timezone

from content.access import LEVEL_MAIN
from content.models import Course, Module, Unit
from content.models.cohort import Cohort
from tests.fixtures import TierSetupMixin, set_membership

User = get_user_model()


class CourseMobileSetupMixin(TierSetupMixin):
    """Mixin providing a course with modules and units for mobile tests."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()

    def setUp(self):
        self.client = Client()
        self.course = Course.objects.create(
            title="A Very Long Course Title That Should Be Truncated on Mobile Screens",
            slug="long-course",
            status="published",
            required_level=LEVEL_MAIN,
            description="Test course for mobile.",
        )
        self.module1 = Module.objects.create(
            course=self.course,
            title="Module With A Very Long Title For Testing Overflow",
            slug="module-1",
            sort_order=1,
        )
        self.unit1 = Unit.objects.create(
            module=self.module1,
            title="Unit One With A Particularly Long Title For Testing Truncation",
            slug="unit-1",
            sort_order=1,
            body="# Lesson content",
        )
        self.unit2 = Unit.objects.create(
            module=self.module1,
            title="Unit Two Also Long",
            slug="unit-2",
            sort_order=2,
            body="# Second lesson",
        )

    def _login_main_user(self):
        user = User.objects.create_user(email="main-mobile@test.com", password="testpass")
        set_membership(user, tier=self.main_tier)
        user.save()
        self.client.login(email="main-mobile@test.com", password="testpass")
        return user


class CourseDetailMobileSyllabusTest(CourseMobileSetupMixin, TestCase):
    """The syllabus sends full titles and counts to mobile readers."""

    def test_module_title_is_not_truncated_in_rendered_content(self):
        response = self.client.get("/courses/long-course")
        self.assertContains(response, "Module With A Very Long Title For Testing Overflow")

    def test_lesson_count_is_in_summary(self):
        response = self.client.get("/courses/long-course")
        self.assertContains(response, 'data-testid="module-lesson-count"')
        self.assertContains(response, "2 lessons")


class CourseDetailMobileCohortTest(CourseMobileSetupMixin, TestCase):
    """Cohort enrollment section stacks on mobile with flex-col."""

    def setUp(self):
        super().setUp()
        self.cohort = Cohort.objects.create(
            course=self.course,
            name="March 2026 Cohort",
            start_date=timezone.now().date(),
            end_date=(timezone.now() + timezone.timedelta(days=30)).date(),
            is_active=True,
        )

    def test_cohort_section_uses_flex_col_on_mobile(self):
        response = self.client.get("/courses/long-course")
        content = response.content.decode()
        self.assertIn("flex-col sm:flex-row sm:items-center sm:justify-between", content)


class CourseUnitBottomNavMobileTest(CourseMobileSetupMixin, TestCase):
    """Bottom prev/next buttons use truncate and stack on mobile."""

    def test_bottom_nav_uses_flex_col(self):
        self._login_main_user()
        response = self.client.get("/courses/long-course/module-1/unit-1")
        self.assertContains(response, "flex-col sm:flex-row items-stretch sm:items-center")

    def test_bottom_prev_title_truncates(self):
        self._login_main_user()
        response = self.client.get("/courses/long-course/module-1/unit-2")
        content = response.content.decode()
        self.assertIn('data-testid="bottom-prev-btn"', content)
        # Title should be wrapped in truncate span
        self.assertIn(
            '<span class="truncate">Unit One With A Particularly Long Title For Testing Truncation</span>',
            content,
        )

    def test_bottom_next_title_truncates(self):
        self._login_main_user()
        response = self.client.get("/courses/long-course/module-1/unit-1")
        content = response.content.decode()
        self.assertIn('data-testid="bottom-next-btn"', content)
        self.assertIn('<span class="truncate">Next: Unit Two Also Long</span>', content)


class CourseUnitSidebarMobileTest(CourseMobileSetupMixin, TestCase):
    """Sidebar navigation has a mobile toggle button."""

    def test_sidebar_toggle_button_exists(self):
        self._login_main_user()
        response = self.client.get("/courses/long-course/module-1/unit-1")
        self.assertContains(response, 'id="sidebar-toggle-btn"')

    def test_sidebar_toggle_is_hidden_on_desktop(self):
        self._login_main_user()
        response = self.client.get("/courses/long-course/module-1/unit-1")
        # Issue #517: the mobile toggle moved into a new wrapper above
        # the breadcrumb (`reader-mobile-progress-bar`), but the
        # ``lg:hidden`` contract is preserved so the bar disappears on
        # desktop. Assert the wrapper carries lg:hidden adjacent to its
        # testid.
        body = response.content.decode()
        wrapper_idx = body.find('data-testid="reader-mobile-progress-bar"')
        self.assertGreater(wrapper_idx, -1)
        snippet = body[max(0, wrapper_idx - 80):wrapper_idx]
        self.assertIn('lg:hidden', snippet)

    def test_sidebar_nav_hidden_on_mobile_by_default(self):
        self._login_main_user()
        response = self.client.get("/courses/long-course/module-1/unit-1")
        # The nav element has hidden lg:block
        self.assertContains(response, 'id="sidebar-nav" class="mt-4 rounded-lg border border-border bg-card p-2 hidden lg:block"')

    def test_sidebar_toggle_has_aria_attributes(self):
        self._login_main_user()
        response = self.client.get("/courses/long-course/module-1/unit-1")
        self.assertContains(response, 'aria-expanded="false"')
        self.assertContains(response, 'aria-controls="sidebar-nav"')


class CourseUnitQaReplyIndentationTest(CourseMobileSetupMixin, TestCase):
    """Q&A reply indentation reduces on mobile (ml-4 sm:ml-8)."""

    def test_reply_indentation_responsive(self):
        self._login_main_user()
        self.unit1.content_id = uuid.uuid4()
        self.unit1.save()
        response = self.client.get("/courses/long-course/module-1/unit-1")
        content = response.content.decode()
        # The JS renders replies with ml-4 sm:ml-8 instead of just ml-8
        self.assertIn("ml-4 sm:ml-8", content)
        # Make sure the old ml-8 only pattern is NOT used
        self.assertNotIn('"ml-8 mt-4', content)
