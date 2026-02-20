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
from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import TestCase, Client
from django.utils import timezone

from content.access import LEVEL_OPEN, LEVEL_BASIC, LEVEL_MAIN, LEVEL_PREMIUM
from content.models import Course, Module, Unit, UserCourseProgress

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
# Model Tests
# ============================================================


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

    def test_str(self):
        course = Course.objects.create(title='My Course', slug='my-course')
        self.assertEqual(str(course), 'My Course')

    def test_get_absolute_url(self):
        course = Course.objects.create(title='Test', slug='test-url')
        self.assertEqual(course.get_absolute_url(), '/courses/test-url')

    def test_unique_slug(self):
        from django.db import IntegrityError
        Course.objects.create(title='First', slug='unique-slug')
        with self.assertRaises(IntegrityError):
            Course.objects.create(title='Second', slug='unique-slug')

    def test_default_values(self):
        course = Course.objects.create(title='Defaults', slug='defaults')
        self.assertEqual(course.description, '')
        self.assertEqual(course.cover_image_url, '')
        self.assertEqual(course.instructor_name, '')
        self.assertEqual(course.instructor_bio, '')
        self.assertEqual(course.required_level, 0)
        self.assertEqual(course.status, 'draft')
        self.assertFalse(course.is_free)
        self.assertEqual(course.discussion_url, '')
        self.assertEqual(course.tags, [])

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
        c1 = Course.objects.create(title='Older', slug='older')
        c2 = Course.objects.create(title='Newer', slug='newer')
        courses = list(Course.objects.all())
        self.assertEqual(courses[0].slug, 'newer')
        self.assertEqual(courses[1].slug, 'older')


class ModuleModelTest(TestCase):
    """Test Module model fields."""

    def setUp(self):
        self.course = Course.objects.create(title='Course', slug='course')

    def test_create_module(self):
        module = Module.objects.create(
            course=self.course, title='Module 1', sort_order=1,
        )
        self.assertEqual(module.title, 'Module 1')
        self.assertEqual(module.course, self.course)
        self.assertEqual(module.sort_order, 1)

    def test_str(self):
        module = Module.objects.create(
            course=self.course, title='Intro', sort_order=0,
        )
        self.assertEqual(str(module), 'Course - Intro')

    def test_ordering_by_sort_order(self):
        Module.objects.create(course=self.course, title='Second', sort_order=2)
        Module.objects.create(course=self.course, title='First', sort_order=1)
        modules = list(Module.objects.filter(course=self.course))
        self.assertEqual(modules[0].title, 'First')
        self.assertEqual(modules[1].title, 'Second')

    def test_cascade_delete(self):
        Module.objects.create(course=self.course, title='Del', sort_order=0)
        self.course.delete()
        self.assertEqual(Module.objects.count(), 0)


class UnitModelTest(TestCase):
    """Test Unit model fields and methods."""

    def setUp(self):
        self.course = Course.objects.create(title='Course', slug='course')
        self.module = Module.objects.create(
            course=self.course, title='Module', sort_order=1,
        )

    def test_create_unit(self):
        unit = Unit.objects.create(
            module=self.module, title='Unit 1', sort_order=1,
        )
        self.assertEqual(unit.title, 'Unit 1')
        self.assertEqual(unit.module, self.module)

    def test_str(self):
        unit = Unit.objects.create(
            module=self.module, title='Lesson 1', sort_order=1,
        )
        self.assertEqual(str(unit), 'Module - Lesson 1')

    def test_default_values(self):
        unit = Unit.objects.create(
            module=self.module, title='Defaults', sort_order=0,
        )
        self.assertEqual(unit.video_url, '')
        self.assertEqual(unit.body, '')
        self.assertEqual(unit.homework, '')
        self.assertEqual(unit.timestamps, [])
        self.assertFalse(unit.is_preview)

    def test_body_markdown_rendered_on_save(self):
        unit = Unit.objects.create(
            module=self.module, title='MD', sort_order=0,
            body='# Lesson\nLearn **this**.',
        )
        self.assertIn('<h1>Lesson</h1>', unit.body_html)
        self.assertIn('<strong>this</strong>', unit.body_html)

    def test_homework_markdown_rendered_on_save(self):
        unit = Unit.objects.create(
            module=self.module, title='HW', sort_order=0,
            homework='## Exercise\nDo **that**.',
        )
        self.assertIn('<h2>Exercise</h2>', unit.homework_html)
        self.assertIn('<strong>that</strong>', unit.homework_html)

    def test_timestamps_json_field(self):
        unit = Unit.objects.create(
            module=self.module, title='TS', sort_order=0,
            timestamps=[
                {'time_seconds': 120, 'label': 'Setting up'},
                {'time_seconds': 300, 'label': 'Building'},
            ],
        )
        self.assertEqual(len(unit.timestamps), 2)
        self.assertEqual(unit.timestamps[0]['label'], 'Setting up')

    def test_get_absolute_url(self):
        unit = Unit.objects.create(
            module=self.module, title='URL Test', sort_order=3,
        )
        self.assertEqual(unit.get_absolute_url(), '/courses/course/1/3')

    def test_ordering_by_sort_order(self):
        Unit.objects.create(module=self.module, title='Second', sort_order=2)
        Unit.objects.create(module=self.module, title='First', sort_order=1)
        units = list(Unit.objects.filter(module=self.module))
        self.assertEqual(units[0].title, 'First')
        self.assertEqual(units[1].title, 'Second')

    def test_cascade_delete_from_module(self):
        Unit.objects.create(module=self.module, title='Del', sort_order=0)
        self.module.delete()
        self.assertEqual(Unit.objects.count(), 0)


class UserCourseProgressModelTest(TestCase):
    """Test UserCourseProgress model."""

    def setUp(self):
        self.user = User.objects.create_user(email='test@example.com')
        self.course = Course.objects.create(title='Course', slug='course')
        self.module = Module.objects.create(
            course=self.course, title='Module', sort_order=1,
        )
        self.unit = Unit.objects.create(
            module=self.module, title='Unit', sort_order=1,
        )

    def test_create_progress(self):
        progress = UserCourseProgress.objects.create(
            user=self.user, unit=self.unit, completed_at=timezone.now(),
        )
        self.assertIsNotNone(progress.completed_at)

    def test_unique_together_user_unit(self):
        from django.db import IntegrityError
        UserCourseProgress.objects.create(
            user=self.user, unit=self.unit, completed_at=timezone.now(),
        )
        with self.assertRaises(IntegrityError):
            UserCourseProgress.objects.create(
                user=self.user, unit=self.unit, completed_at=timezone.now(),
            )

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

    def test_completed_at_nullable(self):
        progress = UserCourseProgress.objects.create(
            user=self.user, unit=self.unit, completed_at=None,
        )
        self.assertIsNone(progress.completed_at)


class CourseTotalAndCompletedTest(TestCase):
    """Test Course.total_units() and Course.completed_units()."""

    def setUp(self):
        self.user = User.objects.create_user(email='progress@example.com')
        self.course = Course.objects.create(
            title='Progress Course', slug='progress',
        )
        self.module = Module.objects.create(
            course=self.course, title='Module', sort_order=1,
        )
        self.unit1 = Unit.objects.create(
            module=self.module, title='Unit 1', sort_order=1,
        )
        self.unit2 = Unit.objects.create(
            module=self.module, title='Unit 2', sort_order=2,
        )
        self.unit3 = Unit.objects.create(
            module=self.module, title='Unit 3', sort_order=3,
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


# ============================================================
# View Tests: /courses catalog
# ============================================================


class CoursesListViewTest(TestCase):
    """Test the /courses catalog page."""

    def setUp(self):
        self.client = Client()
        self.published = Course.objects.create(
            title='Published Course', slug='published-course',
            status='published', instructor_name='Test Instructor',
            is_free=True, tags=['python', 'ai'],
        )
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


# ============================================================
# View Tests: /courses/{slug} detail
# ============================================================


class CourseDetailViewTest(TierSetupMixin, TestCase):
    """Test the /courses/{slug} detail page."""

    def setUp(self):
        self.client = Client()
        self.course = Course.objects.create(
            title='Detail Course', slug='detail-course',
            description='# Course Description\nLearn **great things**.',
            status='published', instructor_name='Jane Doe',
            instructor_bio='Expert in AI.',
            required_level=LEVEL_MAIN,
            tags=['python', 'ml'],
            discussion_url='https://slack.com/channel',
        )
        self.module1 = Module.objects.create(
            course=self.course, title='Getting Started', sort_order=1,
        )
        self.module2 = Module.objects.create(
            course=self.course, title='Advanced Topics', sort_order=2,
        )
        self.unit1 = Unit.objects.create(
            module=self.module1, title='Introduction', sort_order=1,
        )
        self.unit2 = Unit.objects.create(
            module=self.module1, title='Setup', sort_order=2,
        )
        self.unit3 = Unit.objects.create(
            module=self.module2, title='Deep Dive', sort_order=1,
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

    def test_shows_discussion_link(self):
        response = self.client.get('/courses/detail-course')
        self.assertContains(response, 'https://slack.com/channel')
        self.assertContains(response, 'Join the discussion')

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


class CourseDetailAccessControlTest(TierSetupMixin, TestCase):
    """Test access control on course detail page."""

    def setUp(self):
        self.client = Client()
        self.paid_course = Course.objects.create(
            title='Paid Course', slug='paid-course',
            description='Paid course description.',
            status='published', required_level=LEVEL_MAIN,
        )
        self.module = Module.objects.create(
            course=self.paid_course, title='Module 1', sort_order=1,
        )
        self.unit = Unit.objects.create(
            module=self.module, title='Lesson 1', sort_order=1,
        )

    def test_anonymous_sees_syllabus(self):
        response = self.client.get('/courses/paid-course')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Lesson 1')

    def test_anonymous_sees_cta_unlock(self):
        response = self.client.get('/courses/paid-course')
        self.assertContains(response, 'Unlock with Main')

    def test_anonymous_unit_titles_not_clickable(self):
        response = self.client.get('/courses/paid-course')
        content = response.content.decode()
        # Should not have a link to the unit page
        self.assertNotIn(f'href="/courses/paid-course/1/1"', content)

    def test_authorized_user_sees_clickable_links(self):
        user = User.objects.create_user(email='main@test.com', password='testpass')
        user.tier = self.main_tier
        user.save()
        self.client.login(email='main@test.com', password='testpass')
        response = self.client.get('/courses/paid-course')
        self.assertContains(response, 'href="/courses/paid-course/1/1"')

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

    def test_basic_user_cannot_access_main_course(self):
        user = User.objects.create_user(email='basic@test.com', password='testpass')
        user.tier = self.basic_tier
        user.save()
        self.client.login(email='basic@test.com', password='testpass')
        response = self.client.get('/courses/paid-course')
        self.assertContains(response, 'Unlock with Main')

    def test_premium_user_can_access_main_course(self):
        user = User.objects.create_user(email='prem@test.com', password='testpass')
        user.tier = self.premium_tier
        user.save()
        self.client.login(email='prem@test.com', password='testpass')
        response = self.client.get('/courses/paid-course')
        self.assertContains(response, 'href="/courses/paid-course/1/1"')
        self.assertNotContains(response, 'Unlock with Main')


class FreeCourseAccessTest(TierSetupMixin, TestCase):
    """Test free course CTA behavior."""

    def setUp(self):
        self.client = Client()
        self.free_course = Course.objects.create(
            title='Free Course', slug='free-course',
            status='published', is_free=True, required_level=LEVEL_OPEN,
        )
        self.module = Module.objects.create(
            course=self.free_course, title='Module', sort_order=1,
        )
        self.unit = Unit.objects.create(
            module=self.module, title='Free Lesson', sort_order=1,
        )

    def test_anonymous_sees_signup_cta(self):
        response = self.client.get('/courses/free-course')
        self.assertContains(response, 'Sign up free to start this course')

    def test_authenticated_user_no_cta(self):
        user = User.objects.create_user(email='user@test.com', password='testpass')
        self.client.login(email='user@test.com', password='testpass')
        response = self.client.get('/courses/free-course')
        self.assertNotContains(response, 'Sign up free to start this course')

    def test_authenticated_sees_clickable_links(self):
        user = User.objects.create_user(email='user2@test.com', password='testpass')
        self.client.login(email='user2@test.com', password='testpass')
        response = self.client.get('/courses/free-course')
        self.assertContains(response, 'href="/courses/free-course/1/1"')


class CourseProgressDisplayTest(TierSetupMixin, TestCase):
    """Test progress bar display on course detail."""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(email='prog@test.com', password='testpass')
        self.user.tier = self.premium_tier
        self.user.save()

        self.course = Course.objects.create(
            title='Progress Course', slug='progress-course',
            status='published', required_level=LEVEL_OPEN,
        )
        self.module = Module.objects.create(
            course=self.course, title='Module', sort_order=1,
        )
        self.unit1 = Unit.objects.create(
            module=self.module, title='Unit 1', sort_order=1,
        )
        self.unit2 = Unit.objects.create(
            module=self.module, title='Unit 2', sort_order=2,
        )
        self.unit3 = Unit.objects.create(
            module=self.module, title='Unit 3', sort_order=3,
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

    def setUp(self):
        self.client = Client()
        self.course = Course.objects.create(
            title='API Course', slug='api-course',
            status='published', instructor_name='API Instructor',
            tags=['test'], is_free=True,
            cover_image_url='https://example.com/cover.jpg',
        )
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
        paid_course = Course.objects.create(
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

    def setUp(self):
        self.client = Client()
        self.course = Course.objects.create(
            title='API Detail', slug='api-detail',
            description='A detailed course.',
            status='published', instructor_name='Detail Instructor',
            instructor_bio='Bio here.',
            tags=['python'], is_free=False,
            required_level=LEVEL_MAIN,
            discussion_url='https://slack.com/test',
        )
        self.module = Module.objects.create(
            course=self.course, title='Mod 1', sort_order=1,
        )
        self.unit = Unit.objects.create(
            module=self.module, title='Unit 1', sort_order=1,
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


# ============================================================
# Admin Tests
# ============================================================


class CourseAdminTest(TestCase):
    """Test admin CRUD for courses."""

    def setUp(self):
        self.client = Client()
        self.admin_user = User.objects.create_superuser(
            email='admin@test.com', password='testpass',
        )
        self.client.login(email='admin@test.com', password='testpass')

    def test_admin_course_list(self):
        Course.objects.create(
            title='Admin Course', slug='admin-course', status='published',
        )
        response = self.client.get('/admin/content/course/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Admin Course')

    def test_admin_course_add_page(self):
        response = self.client.get('/admin/content/course/add/')
        self.assertEqual(response.status_code, 200)

    def test_admin_module_list(self):
        response = self.client.get('/admin/content/module/')
        self.assertEqual(response.status_code, 200)

    def test_admin_unit_list(self):
        response = self.client.get('/admin/content/unit/')
        self.assertEqual(response.status_code, 200)

    def test_admin_progress_list(self):
        response = self.client.get('/admin/content/usercourseprogress/')
        self.assertEqual(response.status_code, 200)

    def test_admin_publish_action(self):
        course = Course.objects.create(
            title='Draft', slug='draft-action', status='draft',
        )
        self.client.post('/admin/content/course/', {
            'action': 'publish_courses',
            '_selected_action': [course.pk],
        })
        course.refresh_from_db()
        self.assertEqual(course.status, 'published')

    def test_admin_unpublish_action(self):
        course = Course.objects.create(
            title='Published', slug='pub-action', status='published',
        )
        self.client.post('/admin/content/course/', {
            'action': 'unpublish_courses',
            '_selected_action': [course.pk],
        })
        course.refresh_from_db()
        self.assertEqual(course.status, 'draft')

    def test_admin_slug_auto_generated(self):
        from content.admin.course import CourseAdmin
        self.assertEqual(CourseAdmin.prepopulated_fields, {'slug': ('title',)})
