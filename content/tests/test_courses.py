"""Tests for Course Models and Catalog - issue #78.

Covers:
- Course, Module, Unit, UserCourseProgress model fields and constraints
- Markdown rendering on Course description, Unit body and homework
- /courses catalog page (published courses only, badges)
- /courses/{slug} detail page (SEO, syllabus, access control, CTA)
- API endpoints: GET /api/courses, GET /api/courses/{slug}
- Progress tracking for authenticated users
- Admin CRUD for courses
"""

import json

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.db import connection
from django.test import Client, TestCase, tag
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from content.access import LEVEL_MAIN, LEVEL_OPEN
from content.models import (
    Course,
    CourseInstructor,
    Instructor,
    Module,
    Unit,
    UserCourseProgress,
)
from tests.fixtures import TierSetupMixin

User = get_user_model()


def _attach_course_instructor(course, name, bio='', position=0):
    """Attach an Instructor to a Course at the given through-table position.

    Helper for tests written before the legacy ``instructor_name`` /
    ``instructor_bio`` fields were dropped (issue #423). Creates the
    ``Instructor`` row by exact name (or reuses an existing one) and
    inserts the through row.
    """
    instructor, _ = Instructor.objects.get_or_create(
        name=name,
        defaults={
            'instructor_id': name.lower().replace(' ', '-'),
            'bio': bio,
            'status': 'published',
        },
    )
    if bio and not instructor.bio:
        instructor.bio = bio
        instructor.save()
    CourseInstructor.objects.create(
        course=course, instructor=instructor, position=position,
    )
    return instructor


# ============================================================
# Model Tests
# ============================================================


@tag('core')
class CourseModelTest(TestCase):
    """Test Course model fields and methods."""

    def test_create_course_with_required_fields(self):
        course = Course.objects.create(
            title='Test Course',
            slug='test-course',
        )
        self.assertEqual(course.title, 'Test Course')
        self.assertEqual(course.slug, 'test-course')
        self.assertIsNotNone(course.created_at)

    def test_get_absolute_url(self):
        course = Course.objects.create(title='Test', slug='test-url')
        self.assertEqual(course.get_absolute_url(), '/courses/test-url')

    def test_is_published_property(self):
        course = Course.objects.create(
            title='Published', slug='pub', status='published',
        )
        self.assertTrue(course.is_published)

    def test_is_not_published_for_draft(self):
        course = Course.objects.create(
            title='Draft', slug='draft', status='draft',
        )
        self.assertFalse(course.is_published)

    def test_description_markdown_rendered_on_save(self):
        course = Course.objects.create(
            title='MD Test', slug='md-test',
            description='# Hello\nThis is **bold**.',
        )
        self.assertIn('<h1>Hello</h1>', course.description_html)
        self.assertIn('<strong>bold</strong>', course.description_html)

    def test_tags_field_is_list(self):
        course = Course.objects.create(
            title='Tags', slug='tags', tags=['python', 'ai'],
        )
        self.assertEqual(course.tags, ['python', 'ai'])

    def test_required_tier_name_property(self):
        course = Course.objects.create(
            title='Tier', slug='tier', required_level=LEVEL_MAIN,
        )
        self.assertEqual(course.required_tier_name, 'Main')

    def test_ordering_by_created_at_desc(self):
        Course.objects.create(title='Older', slug='older')
        Course.objects.create(title='Newer', slug='newer')
        courses = list(Course.objects.all())
        self.assertEqual(courses[0].slug, 'newer')
        self.assertEqual(courses[1].slug, 'older')


@tag('core')
class ModuleModelTest(TestCase):
    """Test Module model fields."""

    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(title='Course', slug='course')

    def test_create_module(self):
        module = Module.objects.create(
            course=self.course, title='Module 1', slug='module-1', sort_order=1,
        )
        self.assertEqual(module.title, 'Module 1')
        self.assertEqual(module.course, self.course)
        self.assertEqual(module.sort_order, 1)

    def test_ordering_by_sort_order(self):
        Module.objects.create(course=self.course, title='Second', slug='second', sort_order=2)
        Module.objects.create(course=self.course, title='First', slug='first', sort_order=1)
        modules = list(Module.objects.filter(course=self.course))
        self.assertEqual(modules[0].title, 'First')
        self.assertEqual(modules[1].title, 'Second')


@tag('core')
class UnitModelTest(TestCase):
    """Test Unit model fields and methods."""

    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(title='Course', slug='course')
        cls.module = Module.objects.create(
            course=cls.course, title='Module', slug='module', sort_order=1,
        )

    def test_create_unit(self):
        unit = Unit.objects.create(
            module=self.module, title='Unit 1', slug='unit-1', sort_order=1,
        )
        self.assertEqual(unit.title, 'Unit 1')
        self.assertEqual(unit.module, self.module)

    def test_body_markdown_rendered_on_save(self):
        unit = Unit.objects.create(
            module=self.module, title='MD', slug='md', sort_order=0,
            body='# Lesson\nLearn **this**.',
        )
        self.assertIn('<h1>Lesson</h1>', unit.body_html)
        self.assertIn('<strong>this</strong>', unit.body_html)

    def test_homework_markdown_rendered_on_save(self):
        unit = Unit.objects.create(
            module=self.module, title='HW', slug='hw', sort_order=0,
            homework='## Exercise\nDo **that**.',
        )
        self.assertIn('<h2>Exercise</h2>', unit.homework_html)
        self.assertIn('<strong>that</strong>', unit.homework_html)

    def test_timestamps_json_field(self):
        unit = Unit.objects.create(
            module=self.module, title='TS', slug='ts', sort_order=0,
            timestamps=[
                {'time_seconds': 120, 'label': 'Setting up'},
                {'time_seconds': 300, 'label': 'Building'},
            ],
        )
        self.assertEqual(len(unit.timestamps), 2)
        self.assertEqual(unit.timestamps[0]['label'], 'Setting up')

    def test_get_absolute_url(self):
        unit = Unit.objects.create(
            module=self.module, title='URL Test', slug='url-test', sort_order=3,
        )
        self.assertEqual(unit.get_absolute_url(), '/courses/course/module/url-test')

    def test_ordering_by_sort_order(self):
        Unit.objects.create(module=self.module, title='Second', slug='second', sort_order=2)
        Unit.objects.create(module=self.module, title='First', slug='first', sort_order=1)
        units = list(Unit.objects.filter(module=self.module))
        self.assertEqual(units[0].title, 'First')
        self.assertEqual(units[1].title, 'Second')


@tag('core')
class UserCourseProgressModelTest(TestCase):
    """Test UserCourseProgress model."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(email='test@example.com')
        cls.course = Course.objects.create(title='Course', slug='course')
        cls.module = Module.objects.create(
            course=cls.course, title='Module', slug='module', sort_order=1,
        )
        cls.unit = Unit.objects.create(
            module=cls.module, title='Unit', slug='unit', sort_order=1,
        )

    def test_create_progress(self):
        progress = UserCourseProgress.objects.create(
            user=self.user, unit=self.unit, completed_at=timezone.now(),
        )
        self.assertIsNotNone(progress.completed_at)

    def test_str_completed(self):
        progress = UserCourseProgress.objects.create(
            user=self.user, unit=self.unit, completed_at=timezone.now(),
        )
        self.assertIn('completed', str(progress))

    def test_str_in_progress(self):
        progress = UserCourseProgress.objects.create(
            user=self.user, unit=self.unit, completed_at=None,
        )
        self.assertIn('in progress', str(progress))

@tag('core')
class CourseTotalAndCompletedTest(TestCase):
    """Test Course.total_units() and Course.completed_units()."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(email='progress@example.com')
        cls.course = Course.objects.create(
            title='Progress Course', slug='progress',
        )
        cls.module = Module.objects.create(
            course=cls.course, title='Module', slug='module', sort_order=1,
        )
        cls.unit1 = Unit.objects.create(
            module=cls.module, title='Unit 1', slug='unit-1', sort_order=1,
        )
        cls.unit2 = Unit.objects.create(
            module=cls.module, title='Unit 2', slug='unit-2', sort_order=2,
        )
        cls.unit3 = Unit.objects.create(
            module=cls.module, title='Unit 3', slug='unit-3', sort_order=3,
        )

    def test_total_units(self):
        self.assertEqual(self.course.total_units(), 3)

    def test_completed_units_none_completed(self):
        self.assertEqual(self.course.completed_units(self.user), 0)

    def test_completed_units_some_completed(self):
        UserCourseProgress.objects.create(
            user=self.user, unit=self.unit1, completed_at=timezone.now(),
        )
        UserCourseProgress.objects.create(
            user=self.user, unit=self.unit2, completed_at=timezone.now(),
        )
        self.assertEqual(self.course.completed_units(self.user), 2)

    def test_completed_units_anonymous_returns_0(self):
        self.assertEqual(self.course.completed_units(AnonymousUser()), 0)

    def test_completed_units_null_completed_at_not_counted(self):
        UserCourseProgress.objects.create(
            user=self.user, unit=self.unit1, completed_at=None,
        )
        self.assertEqual(self.course.completed_units(self.user), 0)


@tag('core')
class CourseGetNextUnitForTest(TestCase):
    """Test Course.get_next_unit_for(user) — issue #244.

    Returns the first unit in canonical order (module sort_order, then
    unit sort_order) with no UserCourseProgress.completed_at for the
    user. Returns None if all units are done.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(email='nextunit@example.com')
        cls.course = Course.objects.create(
            title='Next Unit Course', slug='next-unit-course',
        )
        # Two modules, three units each, deliberately created out of
        # sort_order to prove the method respects sort_order rather than
        # creation order.
        cls.module2 = Module.objects.create(
            course=cls.course, title='Module 2', slug='module-2', sort_order=2,
        )
        cls.module1 = Module.objects.create(
            course=cls.course, title='Module 1', slug='module-1', sort_order=1,
        )
        # Module 1 units (created out of order)
        cls.m1_u3 = Unit.objects.create(
            module=cls.module1, title='M1 U3', slug='m1-u3', sort_order=3,
        )
        cls.m1_u1 = Unit.objects.create(
            module=cls.module1, title='M1 U1', slug='m1-u1', sort_order=1,
        )
        cls.m1_u2 = Unit.objects.create(
            module=cls.module1, title='M1 U2', slug='m1-u2', sort_order=2,
        )
        # Module 2 units
        cls.m2_u1 = Unit.objects.create(
            module=cls.module2, title='M2 U1', slug='m2-u1', sort_order=1,
        )
        cls.m2_u2 = Unit.objects.create(
            module=cls.module2, title='M2 U2', slug='m2-u2', sort_order=2,
        )
        cls.m2_u3 = Unit.objects.create(
            module=cls.module2, title='M2 U3', slug='m2-u3', sort_order=3,
        )
        cls.canonical_order = [
            cls.m1_u1, cls.m1_u2, cls.m1_u3,
            cls.m2_u1, cls.m2_u2, cls.m2_u3,
        ]

    def _complete(self, *units):
        now = timezone.now()
        for unit in units:
            UserCourseProgress.objects.create(
                user=self.user, unit=unit, completed_at=now,
            )

    def test_no_progress_returns_first_unit_in_canonical_order(self):
        result = self.course.get_next_unit_for(self.user)
        self.assertEqual(result, self.m1_u1)

    def test_after_completing_first_three_units_returns_unit_4(self):
        self._complete(self.m1_u1, self.m1_u2, self.m1_u3)
        result = self.course.get_next_unit_for(self.user)
        self.assertEqual(result, self.m2_u1)

    def test_skipped_units_returns_first_skipped(self):
        # Completed units 1, 3, 5 (skipped 2 and 4) → next is unit 2.
        self._complete(
            self.canonical_order[0],
            self.canonical_order[2],
            self.canonical_order[4],
        )
        result = self.course.get_next_unit_for(self.user)
        self.assertEqual(result, self.canonical_order[1])

    def test_all_completed_returns_none(self):
        self._complete(*self.canonical_order)
        self.assertIsNone(self.course.get_next_unit_for(self.user))

    def test_in_progress_progress_records_count_as_unfinished(self):
        # A UserCourseProgress with completed_at=None means "started but
        # not finished" — it should still be returned as the next unit.
        UserCourseProgress.objects.create(
            user=self.user, unit=self.m1_u1, completed_at=None,
        )
        result = self.course.get_next_unit_for(self.user)
        self.assertEqual(result, self.m1_u1)

    def test_anonymous_user_returns_none(self):
        self.assertIsNone(self.course.get_next_unit_for(AnonymousUser()))

    def test_none_user_returns_none(self):
        self.assertIsNone(self.course.get_next_unit_for(None))

    def test_course_with_no_units_returns_none(self):
        empty = Course.objects.create(title='Empty', slug='empty-course')
        self.assertIsNone(empty.get_next_unit_for(self.user))

    def test_progress_in_other_course_does_not_affect_result(self):
        # Completing a unit in a different course must not be considered.
        other_course = Course.objects.create(title='Other', slug='other')
        other_module = Module.objects.create(
            course=other_course, title='OM', slug='om', sort_order=1,
        )
        other_unit = Unit.objects.create(
            module=other_module, title='OU', slug='ou', sort_order=1,
        )
        self._complete(other_unit)
        result = self.course.get_next_unit_for(self.user)
        self.assertEqual(result, self.m1_u1)

    def test_progress_from_different_user_does_not_affect_result(self):
        other_user = User.objects.create_user(email='other@example.com')
        UserCourseProgress.objects.create(
            user=other_user, unit=self.m1_u1, completed_at=timezone.now(),
        )
        # Our user has no progress, so next unit is still m1_u1.
        result = self.course.get_next_unit_for(self.user)
        self.assertEqual(result, self.m1_u1)


# ============================================================
# View Tests: /courses catalog
# ============================================================


@tag('core')
class CoursesListViewTest(TestCase):
    """Test the /courses catalog page."""

    def setUp(self):
        self.client = Client()
        self.published = Course.objects.create(
            title='Published Course', slug='published-course',
            status='published',
            tags=['python', 'ai'],
        )
        _attach_course_instructor(self.published, 'Test Instructor')
        self.draft = Course.objects.create(
            title='Draft Course', slug='draft-course',
            status='draft',
        )

    def test_returns_200(self):
        response = self.client.get('/courses')
        self.assertEqual(response.status_code, 200)

    def test_shows_published_course(self):
        response = self.client.get('/courses')
        self.assertContains(response, 'Published Course')

    def test_hides_draft_course(self):
        response = self.client.get('/courses')
        self.assertNotContains(response, 'Draft Course')

    def test_shows_instructor_name(self):
        response = self.client.get('/courses')
        self.assertContains(response, 'Test Instructor')

    def test_shows_free_badge(self):
        response = self.client.get('/courses')
        self.assertContains(response, 'Free')

    def test_shows_tag_badges(self):
        response = self.client.get('/courses')
        self.assertContains(response, 'python')
        self.assertContains(response, 'ai')

    def test_uses_correct_template(self):
        response = self.client.get('/courses')
        self.assertTemplateUsed(response, 'content/courses_list.html')

    def test_shows_tier_badge_for_paid_course(self):
        Course.objects.create(
            title='Paid Course', slug='paid-course',
            status='published', required_level=LEVEL_MAIN,
        )
        response = self.client.get('/courses')
        self.assertContains(response, 'Main+')

    def test_empty_catalog_message(self):
        Course.objects.all().delete()
        response = self.client.get('/courses')
        self.assertContains(response, 'No courses available yet')

    def test_shows_cover_image(self):
        self.published.cover_image_url = 'https://example.com/cover.jpg'
        self.published.save()
        response = self.client.get('/courses')
        self.assertContains(response, 'https://example.com/cover.jpg')
        self.assertContains(response, 'alt="Cover image for Published Course"')
        self.assertContains(response, 'loading="lazy"')
        self.assertContains(response, 'data-testid="course-card-preview-image"')
        self.assertNotContains(response, 'data-testid="course-card-preview-fallback"')

    def test_missing_cover_uses_decorative_fallback_preview(self):
        response = self.client.get('/courses')
        body = response.content.decode()
        fallback = body.split(
            'data-testid="course-card-preview-fallback"', 1,
        )[1].split('<div class="min-w-0 p-5', 1)[0]
        self.assertContains(response, 'data-testid="course-card-preview-fallback"')
        self.assertNotIn('Published Course', fallback)
        self.assertNotIn('Test Instructor', fallback)
        self.assertNotIn('python', fallback)
        self.assertContains(response, 'block h-full focus-visible:outline-none')
        self.assertNotContains(response, 'h-12 w-12 text-muted-foreground')


# ============================================================
# View Tests: /courses/{slug} detail
# ============================================================


@tag('core')
class CourseDetailViewTest(TierSetupMixin, TestCase):
    """Test the /courses/{slug} detail page."""

    def setUp(self):
        self.client = Client()
        self.course = Course.objects.create(
            title='Detail Course', slug='detail-course',
            description='# Course Description\nLearn **great things**.',
            status='published',
            required_level=LEVEL_MAIN,
            tags=['python', 'ml'],
            discussion_url='https://slack.com/channel',
        )
        _attach_course_instructor(
            self.course, 'Jane Doe', bio='Expert in AI.',
        )
        self.module1 = Module.objects.create(
            course=self.course, title='Getting Started', slug='getting-started', sort_order=1,
        )
        self.module2 = Module.objects.create(
            course=self.course, title='Advanced Topics', slug='advanced-topics', sort_order=2,
        )
        self.unit1 = Unit.objects.create(
            module=self.module1, title='Introduction', slug='introduction', sort_order=1,
        )
        self.unit2 = Unit.objects.create(
            module=self.module1, title='Setup', slug='setup', sort_order=2,
        )
        self.unit3 = Unit.objects.create(
            module=self.module2, title='Deep Dive', slug='deep-dive', sort_order=1,
        )

    def test_returns_200(self):
        response = self.client.get('/courses/detail-course')
        self.assertEqual(response.status_code, 200)

    def test_uses_correct_template(self):
        response = self.client.get('/courses/detail-course')
        self.assertTemplateUsed(response, 'content/course_detail.html')

    def test_title_tag(self):
        response = self.client.get('/courses/detail-course')
        self.assertContains(response, '<title>Detail Course | AI Shipping Labs</title>')

    def test_shows_course_title(self):
        response = self.client.get('/courses/detail-course')
        self.assertContains(response, 'Detail Course')

    def test_shows_description_html(self):
        response = self.client.get('/courses/detail-course')
        self.assertContains(response, '<h1>Course Description</h1>')
        self.assertContains(response, '<strong>great things</strong>')

    def test_shows_instructor_name(self):
        response = self.client.get('/courses/detail-course')
        self.assertContains(response, 'Jane Doe')

    def test_shows_instructor_bio(self):
        response = self.client.get('/courses/detail-course')
        self.assertContains(response, 'Expert in AI.')

    def test_shows_tags(self):
        response = self.client.get('/courses/detail-course')
        self.assertContains(response, 'python')
        self.assertContains(response, 'ml')

    def test_discussion_link_hidden_for_anonymous(self):
        """Anonymous users don't see the discussion link even if URL is set."""
        response = self.client.get('/courses/detail-course')
        self.assertNotContains(response, 'Join the discussion')

    def test_shows_syllabus_module_titles(self):
        response = self.client.get('/courses/detail-course')
        self.assertContains(response, 'Getting Started')
        self.assertContains(response, 'Advanced Topics')

    def test_shows_syllabus_unit_titles(self):
        response = self.client.get('/courses/detail-course')
        self.assertContains(response, 'Introduction')
        self.assertContains(response, 'Setup')
        self.assertContains(response, 'Deep Dive')

    def test_draft_course_returns_404(self):
        Course.objects.create(
            title='Draft', slug='draft-detail', status='draft',
        )
        response = self.client.get('/courses/draft-detail')
        self.assertEqual(response.status_code, 404)

    def test_nonexistent_course_returns_404(self):
        response = self.client.get('/courses/nonexistent')
        self.assertEqual(response.status_code, 404)

    def test_syllabus_visible_in_html_for_seo(self):
        """Ensure the syllabus is rendered server-side in HTML."""
        response = self.client.get('/courses/detail-course')
        content = response.content.decode()
        self.assertIn('Syllabus', content)
        self.assertIn('Introduction', content)
        self.assertIn('Setup', content)
        self.assertIn('Deep Dive', content)

    def test_missing_cover_uses_decorative_preview(self):
        response = self.client.get('/courses/detail-course')
        self.assertContains(response, 'data-testid="course-detail-preview-fallback"')
        self.assertNotContains(
            response,
            '<h3 class="line-clamp-2 break-words text-base font-semibold '
            'leading-snug text-foreground sm:text-lg">Detail Course</h3>',
            html=True,
        )
        self.assertNotContains(response, 'h-12 w-12 text-muted-foreground')

    def test_cover_image_uses_preview_with_alt_text(self):
        self.course.cover_image_url = 'https://example.com/detail-cover.jpg'
        self.course.save()
        response = self.client.get('/courses/detail-course')
        self.assertContains(response, 'data-testid="course-detail-preview-image"')
        self.assertContains(response, 'https://example.com/detail-cover.jpg')
        self.assertContains(response, 'alt="Cover image for Detail Course"')
        self.assertNotContains(response, 'data-testid="course-detail-preview-fallback"')


@tag('core')
class CourseDetailAccessControlTest(TierSetupMixin, TestCase):
    """Test access control on course detail page."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.paid_course = Course.objects.create(
            title='Paid Course', slug='paid-course',
            description='Paid course description.',
            status='published', required_level=LEVEL_MAIN,
        )
        cls.module = Module.objects.create(
            course=cls.paid_course, title='Module 1', slug='module-1', sort_order=1,
        )
        cls.unit = Unit.objects.create(
            module=cls.module, title='Lesson 1', slug='lesson-1', sort_order=1,
        )

    def test_anonymous_sees_syllabus(self):
        response = self.client.get('/courses/paid-course')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Lesson 1')

    def test_anonymous_sees_cta_unlock(self):
        response = self.client.get('/courses/paid-course')
        self.assertContains(response, 'Unlock with Main')

    def test_anonymous_unit_titles_clickable_for_teaser(self):
        """Issue #248: locked unit rows are clickable so visitors can
        click through to the teaser preview instead of bouncing off
        a non-interactive list."""
        response = self.client.get('/courses/paid-course')
        content = response.content.decode()
        self.assertIn('href="/courses/paid-course/module-1/lesson-1"', content)

    def test_anonymous_locked_unit_row_shows_lock_icon(self):
        """Issue #248: lock icon sets the expectation that the row is gated."""
        response = self.client.get('/courses/paid-course')
        self.assertContains(response, 'data-testid="syllabus-lock-icon"')
        self.assertContains(response, 'data-testid="syllabus-locked-link"')

    def test_authorized_user_sees_clickable_links(self):
        user = User.objects.create_user(email='main@test.com', password='testpass')
        user.tier = self.main_tier
        user.save()
        self.client.login(email='main@test.com', password='testpass')
        response = self.client.get('/courses/paid-course')
        self.assertContains(response, 'href="/courses/paid-course/module-1/lesson-1"')

    def test_authorized_user_sees_progress_bar(self):
        user = User.objects.create_user(email='main2@test.com', password='testpass')
        user.tier = self.main_tier
        user.save()
        self.client.login(email='main2@test.com', password='testpass')
        response = self.client.get('/courses/paid-course')
        self.assertContains(response, 'Your Progress')
        self.assertContains(response, '0 of 1 completed')

    def test_unauthorized_user_no_progress_bar(self):
        response = self.client.get('/courses/paid-course')
        self.assertNotContains(response, 'Your Progress')

    # Per-tier matrix on /courses/{slug} (basic vs main, premium vs main)
    # removed in #261: covered end-to-end by
    # `playwright_tests/test_access_control.py::TestScenario4MainMemberReadsUpToLevelBlockedOnPremium`
    # and `TestScenario8AnonymousEvaluatesGatedCourseSyllabus`. The
    # access function unit tests in `test_access_control.py::CanAccessTest`
    # remain authoritative at the Django layer.


@tag('core')
class FreeCourseAccessTest(TierSetupMixin, TestCase):
    """Test free course CTA behavior."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.free_course = Course.objects.create(
            title='Free Course', slug='free-course',
            status='published', required_level=LEVEL_OPEN,
        )
        cls.module = Module.objects.create(
            course=cls.free_course, title='Module', slug='module', sort_order=1,
        )
        cls.unit = Unit.objects.create(
            module=cls.module, title='Free Lesson', slug='free-lesson', sort_order=1,
        )

    def test_anonymous_sees_signup_cta(self):
        response = self.client.get('/courses/free-course')
        self.assertContains(response, 'Sign up free to start this course')

    def test_authenticated_user_no_cta(self):
        User.objects.create_user(email='user@test.com', password='testpass')
        self.client.login(email='user@test.com', password='testpass')
        response = self.client.get('/courses/free-course')
        self.assertNotContains(response, 'Sign up free to start this course')

    def test_authenticated_sees_clickable_links(self):
        User.objects.create_user(email='user2@test.com', password='testpass')
        self.client.login(email='user2@test.com', password='testpass')
        response = self.client.get('/courses/free-course')
        self.assertContains(response, 'href="/courses/free-course/module/free-lesson"')


class CourseProgressDisplayTest(TierSetupMixin, TestCase):
    """Test progress bar display on course detail."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = User.objects.create_user(email='prog@test.com', password='testpass')
        cls.user.tier = cls.premium_tier
        cls.user.save()

        cls.course = Course.objects.create(
            title='Progress Course', slug='progress-course',
            status='published', required_level=LEVEL_OPEN,
        )
        cls.module = Module.objects.create(
            course=cls.course, title='Module', slug='module', sort_order=1,
        )
        cls.unit1 = Unit.objects.create(
            module=cls.module, title='Unit 1', slug='unit-1', sort_order=1,
        )
        cls.unit2 = Unit.objects.create(
            module=cls.module, title='Unit 2', slug='unit-2', sort_order=2,
        )
        cls.unit3 = Unit.objects.create(
            module=cls.module, title='Unit 3', slug='unit-3', sort_order=3,
        )

    def test_shows_progress_count(self):
        UserCourseProgress.objects.create(
            user=self.user, unit=self.unit1, completed_at=timezone.now(),
        )
        self.client.login(email='prog@test.com', password='testpass')
        response = self.client.get('/courses/progress-course')
        self.assertContains(response, '1 of 3 completed')

    def test_shows_completed_checkmark(self):
        UserCourseProgress.objects.create(
            user=self.user, unit=self.unit1, completed_at=timezone.now(),
        )
        self.client.login(email='prog@test.com', password='testpass')
        response = self.client.get('/courses/progress-course')
        self.assertContains(response, 'check-circle-2')


# ============================================================
# API Tests
# ============================================================


class ApiCoursesListTest(TierSetupMixin, TestCase):
    """Test GET /api/courses."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.course = Course.objects.create(
            title='API Course', slug='api-course',
            status='published',
            tags=['test'],
            cover_image_url='https://example.com/cover.jpg',
        )
        _attach_course_instructor(cls.course, 'API Instructor')
        Course.objects.create(
            title='Draft API', slug='draft-api', status='draft',
        )

    def test_returns_200(self):
        response = self.client.get('/api/courses')
        self.assertEqual(response.status_code, 200)

    def test_returns_json(self):
        response = self.client.get('/api/courses')
        self.assertEqual(response['Content-Type'], 'application/json')

    def test_only_published_courses(self):
        response = self.client.get('/api/courses')
        data = json.loads(response.content)
        self.assertEqual(len(data['courses']), 1)
        self.assertEqual(data['courses'][0]['slug'], 'api-course')

    def test_includes_is_locked_flag_anonymous(self):
        Course.objects.create(
            title='Paid', slug='paid-api',
            status='published', required_level=LEVEL_MAIN,
        )
        response = self.client.get('/api/courses')
        data = json.loads(response.content)
        courses_by_slug = {c['slug']: c for c in data['courses']}
        self.assertFalse(courses_by_slug['api-course']['is_locked'])
        self.assertTrue(courses_by_slug['paid-api']['is_locked'])

    def test_includes_course_fields(self):
        response = self.client.get('/api/courses')
        data = json.loads(response.content)
        course = data['courses'][0]
        self.assertEqual(course['title'], 'API Course')
        self.assertEqual(course['instructor_name'], 'API Instructor')
        self.assertEqual(course['tags'], ['test'])
        self.assertTrue(course['is_free'])
        self.assertEqual(course['cover_image_url'], 'https://example.com/cover.jpg')

    def test_authenticated_user_is_locked_reflects_tier(self):
        Course.objects.create(
            title='Main Course', slug='main-api',
            status='published', required_level=LEVEL_MAIN,
        )
        user = User.objects.create_user(email='main@test.com', password='testpass')
        user.tier = self.main_tier
        user.save()
        self.client.login(email='main@test.com', password='testpass')
        response = self.client.get('/api/courses')
        data = json.loads(response.content)
        courses_by_slug = {c['slug']: c for c in data['courses']}
        self.assertFalse(courses_by_slug['main-api']['is_locked'])


class ApiCourseDetailTest(TierSetupMixin, TestCase):
    """Test GET /api/courses/{slug}."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.course = Course.objects.create(
            title='API Detail', slug='api-detail',
            description='A detailed course.',
            status='published',
            tags=['python'],
            required_level=LEVEL_MAIN,
            discussion_url='https://slack.com/test',
        )
        _attach_course_instructor(
            cls.course, 'Detail Instructor', bio='Bio here.',
        )
        cls.module = Module.objects.create(
            course=cls.course, title='Mod 1', slug='mod-1', sort_order=1,
        )
        cls.unit = Unit.objects.create(
            module=cls.module, title='Unit 1', slug='unit-1', sort_order=1,
            is_preview=True,
        )

    def test_returns_200(self):
        response = self.client.get('/api/courses/api-detail')
        self.assertEqual(response.status_code, 200)

    def test_returns_json(self):
        response = self.client.get('/api/courses/api-detail')
        self.assertEqual(response['Content-Type'], 'application/json')

    def test_includes_course_detail_fields(self):
        response = self.client.get('/api/courses/api-detail')
        data = json.loads(response.content)
        self.assertEqual(data['title'], 'API Detail')
        self.assertEqual(data['description'], 'A detailed course.')
        self.assertEqual(data['instructor_name'], 'Detail Instructor')
        self.assertEqual(data['instructor_bio'], 'Bio here.')
        self.assertEqual(data['tags'], ['python'])
        self.assertEqual(data['discussion_url'], 'https://slack.com/test')

    def test_includes_syllabus(self):
        response = self.client.get('/api/courses/api-detail')
        data = json.loads(response.content)
        self.assertEqual(len(data['syllabus']), 1)
        self.assertEqual(data['syllabus'][0]['title'], 'Mod 1')
        self.assertEqual(len(data['syllabus'][0]['units']), 1)
        self.assertEqual(data['syllabus'][0]['units'][0]['title'], 'Unit 1')
        self.assertTrue(data['syllabus'][0]['units'][0]['is_preview'])

    def test_anonymous_no_progress(self):
        response = self.client.get('/api/courses/api-detail')
        data = json.loads(response.content)
        self.assertNotIn('progress', data)

    def test_authenticated_includes_progress(self):
        user = User.objects.create_user(email='prog@test.com', password='testpass')
        user.tier = self.main_tier
        user.save()
        UserCourseProgress.objects.create(
            user=user, unit=self.unit, completed_at=timezone.now(),
        )
        self.client.login(email='prog@test.com', password='testpass')
        response = self.client.get('/api/courses/api-detail')
        data = json.loads(response.content)
        self.assertEqual(data['progress']['completed'], 1)
        self.assertEqual(data['progress']['total'], 1)

    def test_is_locked_for_anonymous(self):
        response = self.client.get('/api/courses/api-detail')
        data = json.loads(response.content)
        self.assertTrue(data['is_locked'])

    def test_not_locked_for_authorized_user(self):
        user = User.objects.create_user(email='main@test.com', password='testpass')
        user.tier = self.main_tier
        user.save()
        self.client.login(email='main@test.com', password='testpass')
        response = self.client.get('/api/courses/api-detail')
        data = json.loads(response.content)
        self.assertFalse(data['is_locked'])

    def test_nonexistent_course_returns_404(self):
        response = self.client.get('/api/courses/nonexistent')
        self.assertEqual(response.status_code, 404)

    def test_draft_course_returns_404(self):
        Course.objects.create(
            title='Draft', slug='draft-api-detail', status='draft',
        )
        response = self.client.get('/api/courses/draft-api-detail')
        self.assertEqual(response.status_code, 404)


class CourseTestimonialsViewTest(TestCase):
    """Test testimonials section on course detail page."""

    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(
            title='Testimonial Course', slug='testimonial-course',
            status='published',
            testimonials=[
                {'quote': 'Great course!', 'name': 'Alice', 'role': 'Engineer', 'company': 'Acme'},
                {'quote': 'Learned a lot.', 'name': 'Bob', 'source_url': 'https://example.com/bob'},
            ],
        )
        cls.module = Module.objects.create(
            course=cls.course, title='Mod', slug='mod', sort_order=1,
        )
        cls.unit = Unit.objects.create(
            module=cls.module, title='Unit', slug='unit', sort_order=1,
        )

    def test_heading_shown(self):
        response = self.client.get('/courses/testimonial-course')
        self.assertContains(response, 'What learners say')

    def test_quotes_rendered(self):
        response = self.client.get('/courses/testimonial-course')
        self.assertContains(response, 'Great course!')
        self.assertContains(response, 'Learned a lot.')

    def test_names_rendered(self):
        response = self.client.get('/courses/testimonial-course')
        self.assertContains(response, 'Alice')
        self.assertContains(response, 'Bob')

    def test_role_and_company_rendered(self):
        response = self.client.get('/courses/testimonial-course')
        self.assertContains(response, 'Engineer')
        self.assertContains(response, 'Acme')

    def test_source_url_as_link(self):
        response = self.client.get('/courses/testimonial-course')
        self.assertContains(response, 'href="https://example.com/bob"')
        self.assertContains(response, 'focus-visible:outline-accent')

    def test_testimonials_use_shared_card_grid(self):
        response = self.client.get('/courses/testimonial-course')
        self.assertContains(response, 'data-testid="testimonial-grid"')
        self.assertContains(response, 'data-testid="testimonial-card"', count=2)
        self.assertContains(response, 'data-testid="testimonial-author"', count=2)
        self.assertContains(response, 'md:grid-cols-2')
        self.assertNotContains(response, 'columns-1')

    def test_long_role_and_company_can_wrap(self):
        self.course.testimonials = [
            {
                'quote': 'Dense and practical.',
                'name': 'Long Metadata Learner',
                'role': 'Principal Applied AI Systems Reliability Engineer',
                'company': 'Very Long Company Name for Enterprise Research Platforms',
            },
        ]
        self.course.save(update_fields=['testimonials'])
        response = self.client.get('/courses/testimonial-course')
        self.assertContains(response, 'break-words')
        self.assertContains(response, 'Principal Applied AI Systems Reliability Engineer')
        self.assertContains(response, 'Very Long Company Name for Enterprise Research Platforms')

    def test_no_section_when_empty(self):
        course = Course.objects.create(
            title='No Testimonials', slug='no-testimonials',
            status='published', testimonials=[],
        )
        Module.objects.create(course=course, title='M', slug='m', sort_order=1)
        response = self.client.get('/courses/no-testimonials')
        self.assertNotContains(response, 'What learners say')

    def test_context_has_testimonials(self):
        response = self.client.get('/courses/testimonial-course')
        self.assertEqual(len(response.context['testimonials']), 2)


class DiscussionButtonTierRestrictionTest(TierSetupMixin, TestCase):
    """Test that the discussion button is only visible to Main+ tier users.

    Covers issue #153: free-tier and anonymous users must not see the
    discussion link, even on courses that have a discussion_url set.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.free_course = Course.objects.create(
            title='Free With Discussion', slug='free-with-discussion',
            status='published', required_level=LEVEL_OPEN,
            discussion_url='https://slack.com/free-channel',
        )
        Module.objects.create(
            course=cls.free_course, title='Mod', slug='mod', sort_order=1,
        )
        cls.paid_course = Course.objects.create(
            title='Paid With Discussion', slug='paid-with-discussion',
            status='published', required_level=LEVEL_MAIN,
            discussion_url='https://slack.com/paid-channel',
        )
        Module.objects.create(
            course=cls.paid_course, title='Mod', slug='mod', sort_order=1,
        )

    def test_anonymous_does_not_see_discussion_on_free_course(self):
        response = self.client.get('/courses/free-with-discussion')
        self.assertNotContains(response, 'Join the discussion')

    def test_free_tier_user_does_not_see_discussion(self):
        user = User.objects.create_user(email='free-disc@test.com', password='testpass')
        user.tier = self.free_tier
        user.save()
        self.client.login(email='free-disc@test.com', password='testpass')
        response = self.client.get('/courses/free-with-discussion')
        self.assertNotContains(response, 'Join the discussion')

    def test_basic_tier_user_does_not_see_discussion(self):
        user = User.objects.create_user(email='basic-disc@test.com', password='testpass')
        user.tier = self.basic_tier
        user.save()
        self.client.login(email='basic-disc@test.com', password='testpass')
        response = self.client.get('/courses/free-with-discussion')
        self.assertNotContains(response, 'Join the discussion')

    def test_main_tier_user_not_on_free_course(self):
        """Main user does NOT see discussion on free course (Slack is paid-only)."""
        user = User.objects.create_user(email='main-disc@test.com', password='testpass')
        user.tier = self.main_tier
        user.save()
        self.client.login(email='main-disc@test.com', password='testpass')
        response = self.client.get('/courses/free-with-discussion')
        self.assertNotContains(response, 'Join the discussion')

    def test_main_tier_user_sees_discussion_on_paid(self):
        """Main user sees discussion on paid course."""
        user = User.objects.create_user(email='main-disc2@test.com', password='testpass')
        user.tier = self.main_tier
        user.save()
        self.client.login(email='main-disc2@test.com', password='testpass')
        response = self.client.get('/courses/paid-with-discussion')
        self.assertContains(response, 'Join the discussion')

    def test_premium_tier_user_sees_discussion_on_paid(self):
        user = User.objects.create_user(email='prem-disc@test.com', password='testpass')
        user.tier = self.premium_tier
        user.save()
        self.client.login(email='prem-disc@test.com', password='testpass')
        response = self.client.get('/courses/paid-with-discussion')
        self.assertContains(response, 'Join the discussion')

    def test_main_tier_sees_discussion_on_paid_course(self):
        user = User.objects.create_user(email='main-paid@test.com', password='testpass')
        user.tier = self.main_tier
        user.save()
        self.client.login(email='main-paid@test.com', password='testpass')
        response = self.client.get('/courses/paid-with-discussion')
        self.assertContains(response, 'Join the discussion')

    def test_no_discussion_url_hides_button_even_for_main(self):
        course = Course.objects.create(
            title='No Discussion', slug='no-discussion',
            status='published', discussion_url='',
        )
        Module.objects.create(course=course, title='M', slug='m', sort_order=1)
        user = User.objects.create_user(email='main-nodisc@test.com', password='testpass')
        user.tier = self.main_tier
        user.save()
        self.client.login(email='main-nodisc@test.com', password='testpass')
        response = self.client.get('/courses/no-discussion')
        self.assertNotContains(response, 'Join the discussion')


@tag('core')
class CourseIsFreePropertyTests(TestCase):
    """`Course.is_free` is derived from `required_level == 0`."""

    def test_required_level_zero_is_free(self):
        course = Course(required_level=0)
        self.assertTrue(course.is_free)

    def test_required_level_basic_is_not_free(self):
        course = Course(required_level=10)
        self.assertFalse(course.is_free)

    def test_required_level_main_is_not_free(self):
        course = Course(required_level=20)
        self.assertFalse(course.is_free)

    def test_required_level_premium_is_not_free(self):
        course = Course(required_level=30)
        self.assertFalse(course.is_free)


# --- Conversions from playwright_tests/test_seo_tags.py (issue #256) ---


class CourseTagFilterTest(TestCase):
    """Behaviour previously covered by Playwright Scenario 6 on /courses.
    Filtering happens via the ?tag= query param and resolves server-side.
    """

    def test_tag_filter_on_courses(self):
        # Replaces playwright_tests/test_seo_tags.py::TestScenario6TagFiltersAcrossPages::test_tag_filter_on_courses
        Course.objects.create(
            title='Python Course', slug='python-course',
            tags=['python'], status='published',
        )
        Course.objects.create(
            title='Go Course', slug='go-course',
            tags=['go'], status='published',
        )

        response = self.client.get('/courses?tag=python')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['selected_tags'], ['python'])
        self.assertContains(response, 'Python Course')
        self.assertNotContains(response, 'Go Course')


# ============================================================
# Issue #282: per-module "completed/total lessons" header
# ============================================================


class CourseModuleProgressLabelTest(TierSetupMixin, TestCase):
    """Module headers show ``completed/total lessons`` once the user has
    progress in that module; plain ``total lessons`` otherwise.

    Issue #282 — the count next to each module summary on the course
    detail page becomes a fraction (e.g. ``3/10 lessons``) the moment the
    learner ticks off any lesson in the module. Anonymous users and users
    with zero progress see the original ``10 lessons`` form.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.course = Course.objects.create(
            title='Module Progress Course',
            slug='module-progress-course',
            status='published',
            required_level=LEVEL_OPEN,
        )
        # Module 1: 3 lessons — user completes one of them.
        cls.module1 = Module.objects.create(
            course=cls.course, title='Module One', slug='module-one', sort_order=1,
        )
        cls.m1_units = [
            Unit.objects.create(
                module=cls.module1, title=f'M1 Unit {i}',
                slug=f'm1-unit-{i}', sort_order=i,
            )
            for i in range(1, 4)
        ]
        # Module 2: 2 lessons — user completes none.
        cls.module2 = Module.objects.create(
            course=cls.course, title='Module Two', slug='module-two', sort_order=2,
        )
        cls.m2_units = [
            Unit.objects.create(
                module=cls.module2, title=f'M2 Unit {i}',
                slug=f'm2-unit-{i}', sort_order=i,
            )
            for i in range(1, 3)
        ]

    def _login_with_progress_in_module1(self, completed_count=1):
        user = User.objects.create_user(email='m1prog@test.com', password='pw')
        for unit in self.m1_units[:completed_count]:
            UserCourseProgress.objects.create(
                user=user, unit=unit, completed_at=timezone.now(),
            )
        self.client.login(email='m1prog@test.com', password='pw')
        return user

    def test_authenticated_user_with_progress_sees_fraction(self):
        """User who completed 1 of 3 lessons in module 1 sees ``1/3 lessons``."""
        self._login_with_progress_in_module1(completed_count=1)
        response = self.client.get('/courses/module-progress-course')
        self.assertContains(response, '1/3 lessons')

    def test_authenticated_user_no_progress_in_module_sees_total_only(self):
        """Module 2 has 0 progress, so its header still reads ``2 lessons``."""
        self._login_with_progress_in_module1(completed_count=1)
        response = self.client.get('/courses/module-progress-course')
        # Module 2 header — no fraction, just the total.
        self.assertContains(response, '2 lessons')
        # Cross-check: the bare ``3 lessons`` form (module 1 totals before
        # progress) must NOT appear, because module 1 now uses the fraction.
        self.assertNotContains(response, '>3 lessons<')

    def test_authenticated_user_with_zero_progress_sees_total_only(self):
        """User logged in but no progress at all → all modules show plain totals."""
        User.objects.create_user(email='zero@test.com', password='pw')
        self.client.login(email='zero@test.com', password='pw')
        response = self.client.get('/courses/module-progress-course')
        self.assertContains(response, '3 lessons')
        self.assertContains(response, '2 lessons')
        # No fraction anywhere in the syllabus.
        self.assertNotContains(response, '/3 lessons')
        self.assertNotContains(response, '/2 lessons')

    def test_anonymous_user_sees_total_only(self):
        """Anonymous visitors have no progress to show — totals only."""
        response = self.client.get('/courses/module-progress-course')
        self.assertContains(response, '3 lessons')
        self.assertContains(response, '2 lessons')
        self.assertNotContains(response, '/3 lessons')
        self.assertNotContains(response, '/2 lessons')

    def test_all_lessons_completed_renders_full_fraction(self):
        """Completing every lesson in a module shows e.g. ``3/3 lessons`` —
        the spec explicitly chose the fraction form over a separate
        ``Complete`` label."""
        self._login_with_progress_in_module1(completed_count=3)
        response = self.client.get('/courses/module-progress-course')
        self.assertContains(response, '3/3 lessons')

    def test_completed_count_by_module_in_context(self):
        """The view exposes the per-module completion count dict for templates."""
        user = self._login_with_progress_in_module1(completed_count=2)
        response = self.client.get('/courses/module-progress-course')
        ctx = response.context['completed_count_by_module']
        self.assertEqual(ctx[self.module1.id], 2)
        # Module 2 has no progress — absence (or 0) both render plain.
        self.assertNotIn(self.module2.id, ctx)
        # Sanity: the user fixture is the one we logged in as.
        self.assertEqual(user.email, 'm1prog@test.com')

    def test_anonymous_user_gets_empty_completion_dict(self):
        """No DB lookup for anonymous users — an empty dict is enough for
        the template to fall through to the plain ``X lessons`` form."""
        response = self.client.get('/courses/module-progress-course')
        self.assertEqual(response.context['completed_count_by_module'], {})

    def test_progress_in_other_user_does_not_leak(self):
        """Completion rows for a different user must not appear in this
        user's per-module count."""
        other = User.objects.create_user(email='other@test.com', password='pw')
        for unit in self.m1_units:
            UserCourseProgress.objects.create(
                user=other, unit=unit, completed_at=timezone.now(),
            )
        # Logged-in user has no progress.
        User.objects.create_user(email='clean@test.com', password='pw')
        self.client.login(email='clean@test.com', password='pw')
        response = self.client.get('/courses/module-progress-course')
        # Should still see the plain totals, not 3/3.
        self.assertContains(response, '3 lessons')
        self.assertNotContains(response, '3/3 lessons')


class CourseDetailModuleCountQueryGuardTest(TierSetupMixin, TestCase):
    """N+1 guard for the per-module ``completed/total`` lookup (issue #282).

    The per-module completion lookup must be a single query regardless of
    how many modules the course has. This test creates a course with many
    modules and asserts that doubling the module count does not double the
    progress-related query count.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = User.objects.create_user(email='nplus1@test.com', password='pw')

    def _make_course(self, slug, module_count, units_per_module=2):
        course = Course.objects.create(
            title=f'Course {slug}', slug=slug,
            status='published', required_level=LEVEL_OPEN,
        )
        for i in range(module_count):
            module = Module.objects.create(
                course=course, title=f'M{i}', slug=f'm-{i}', sort_order=i,
            )
            for j in range(units_per_module):
                unit = Unit.objects.create(
                    module=module, title=f'U{i}-{j}',
                    slug=f'u-{i}-{j}', sort_order=j,
                )
                # Complete the first unit of every module so the per-module
                # count branch is actually exercised.
                if j == 0:
                    UserCourseProgress.objects.create(
                        user=self.user, unit=unit, completed_at=timezone.now(),
                    )
        return course

    def test_progress_lookup_query_count_constant(self):
        """Going from 3 to 9 modules must not 3x the query count of the
        per-module completion branch — there must be a single batched
        progress query, not one per module."""
        small_course = self._make_course('small-course', module_count=3)
        large_course = self._make_course('large-course', module_count=9)

        self.client.login(email='nplus1@test.com', password='pw')
        # Warm up the session/middleware so we don't measure unrelated
        # one-off queries (CSRF token, session create, etc.).
        self.client.get(f'/courses/{small_course.slug}')

        with CaptureQueriesContext(connection) as small_ctx:
            r1 = self.client.get(f'/courses/{small_course.slug}')
        self.assertEqual(r1.status_code, 200)

        with CaptureQueriesContext(connection) as large_ctx:
            r2 = self.client.get(f'/courses/{large_course.slug}')
        self.assertEqual(r2.status_code, 200)

        # 6 extra modules must not produce 6 extra queries. Allow a small
        # slack for incidental per-module queries elsewhere on the page,
        # but the progress lookup itself must be batched (1 query).
        delta = len(large_ctx.captured_queries) - len(small_ctx.captured_queries)
        self.assertLess(
            delta, 6,
            msg=(
                f'Course detail page query count grew by {delta} when going '
                f'from 3 to 9 modules — the per-module progress lookup is '
                f'likely N+1.\nSmall queries: {len(small_ctx.captured_queries)}, '
                f'large queries: {len(large_ctx.captured_queries)}'
            ),
        )


class ApiCourseDetailQueryGuardTest(TierSetupMixin, TestCase):
    """N+1 regression guard for ``GET /api/courses/{slug}`` (issue #287).

    Before the fix, ``api_course_detail`` chained ``.order_by('sort_order')``
    on each module's prefetched units queryset, which busts the prefetch
    cache and forces a fresh ``SELECT`` per module. This made the JSON API
    grow linearly with module count (9 → 14 queries when going from a
    5-module course to a 10-module course).

    The fix pushes unit ordering into the prefetch via
    ``Prefetch('units', queryset=Unit.objects.order_by('sort_order'))`` in
    ``Course.get_syllabus()``, and drops the redundant ``.order_by()`` in
    the view. This test asserts:

    1. Anonymous and authenticated query counts are below the targets
       called out in the issue (≤6 anon, ≤9 auth).
    2. The query count is constant across a 5-module course and a
       10-module course — the only way that can hold is if the per-unit
       fetch is a single batched prefetch, not one query per module.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = User.objects.create_user(
            email='api-nplus1@test.com', password='pw',
        )
        cls.small = cls._make_course('api-q-small', n_modules=5, n_units=4)
        cls.large = cls._make_course('api-q-large', n_modules=10, n_units=5)

    @staticmethod
    def _make_course(slug, n_modules, n_units):
        course = Course.objects.create(
            title=slug, slug=slug, status='published',
            required_level=LEVEL_OPEN,
        )
        for m_i in range(n_modules):
            module = Module.objects.create(
                course=course, title=f'M{m_i}',
                slug=f'm-{m_i}', sort_order=m_i,
            )
            for u_i in range(n_units):
                Unit.objects.create(
                    module=module, title=f'U{m_i}-{u_i}',
                    slug=f'u-{m_i}-{u_i}', sort_order=u_i,
                )
        return course

    def _measure(self, client, url):
        # Warm up auth/session middleware so the first call's one-off
        # queries (session create, etc.) don't pollute the measurement.
        client.get(url)
        with CaptureQueriesContext(connection) as ctx:
            response = client.get(url)
        self.assertEqual(response.status_code, 200)
        return len(ctx.captured_queries)

    def test_anonymous_query_count_is_constant_and_below_target(self):
        client = Client()
        small_count = self._measure(client, f'/api/courses/{self.small.slug}')
        large_count = self._measure(client, f'/api/courses/{self.large.slug}')

        self.assertLessEqual(
            small_count, 6,
            msg=(
                f'Anonymous /api/courses/{self.small.slug} took '
                f'{small_count} queries (expected ≤ 6 — was 9 before the '
                f'fix). Issue #287.'
            ),
        )
        self.assertLessEqual(
            large_count, 6,
            msg=(
                f'Anonymous /api/courses/{self.large.slug} took '
                f'{large_count} queries (expected ≤ 6 — was 14 before the '
                f'fix). Issue #287.'
            ),
        )
        self.assertEqual(
            small_count, large_count,
            msg=(
                f'Anonymous /api/courses/{{slug}} query count must be '
                f'constant in module count — got {small_count} for the '
                f'5-module course and {large_count} for the 10-module '
                f'course. The N+1 from chaining .order_by() on a '
                f'prefetched relation has likely returned. Issue #287.'
            ),
        )

    def test_authenticated_query_count_is_constant_and_below_target(self):
        client = Client()
        client.login(email='api-nplus1@test.com', password='pw')
        small_count = self._measure(client, f'/api/courses/{self.small.slug}')
        large_count = self._measure(client, f'/api/courses/{self.large.slug}')

        # Bound includes the M2M ordered_instructors fetch added by
        # issue #423 (which dropped the legacy instructor_name / _bio
        # mirror fields and now reads first-class Instructor rows).
        self.assertLessEqual(
            small_count, 10,
            msg=(
                f'Authenticated /api/courses/{self.small.slug} took '
                f'{small_count} queries (expected ≤ 10 — was 12 before '
                f'fix #287; issue #423 added a single M2M fetch). '
                f'Issue #287 / #423.'
            ),
        )
        self.assertLessEqual(
            large_count, 10,
            msg=(
                f'Authenticated /api/courses/{self.large.slug} took '
                f'{large_count} queries (expected ≤ 10 — was 17 before '
                f'fix #287; issue #423 added a single M2M fetch). '
                f'Issue #287 / #423.'
            ),
        )
        self.assertEqual(
            small_count, large_count,
            msg=(
                f'Authenticated /api/courses/{{slug}} query count must be '
                f'constant in module count — got {small_count} for the '
                f'5-module course and {large_count} for the 10-module '
                f'course. The N+1 from chaining .order_by() on a '
                f'prefetched relation has likely returned. Issue #287.'
            ),
        )
