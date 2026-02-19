"""Tests for Course Unit Pages and Progress Tracking - issue #79.

Covers:
- /courses/{slug}/{module_sort}/{unit_sort} unit page view
- Access control: preview units open to all, gated units check tier
- Video player, lesson text, homework rendering on unit page
- Sidebar navigation with completed checkmarks
- "Mark as completed" toggle (creates/deletes UserCourseProgress)
- "Next unit" button navigation across module boundaries
- API: GET /api/courses/{slug}/units/{unit_id}
- API: POST /api/courses/{slug}/units/{unit_id}/complete
"""

import json

from django.contrib.auth import get_user_model
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


class CourseUnitSetupMixin(TierSetupMixin):
    """Mixin providing a standard course with modules and units."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()

    def setUp(self):
        self.client = Client()
        self.course = Course.objects.create(
            title='Test Course', slug='test-course',
            status='published', required_level=LEVEL_MAIN,
            description='A paid course.',
        )
        self.module1 = Module.objects.create(
            course=self.course, title='Module 1', sort_order=1,
        )
        self.module2 = Module.objects.create(
            course=self.course, title='Module 2', sort_order=2,
        )
        self.unit1 = Unit.objects.create(
            module=self.module1, title='Lesson 1', sort_order=1,
            body='# Introduction\nThis is the **first** lesson.',
            homework='## Exercise 1\nDo **this**.',
            video_url='https://www.youtube.com/watch?v=dQw4w9WgXcB',
            timestamps=[
                {'time_seconds': 0, 'label': 'Intro'},
                {'time_seconds': 120, 'label': 'Setup'},
            ],
        )
        self.unit2 = Unit.objects.create(
            module=self.module1, title='Lesson 2', sort_order=2,
            body='# Second lesson\nMore content.',
        )
        self.unit3 = Unit.objects.create(
            module=self.module2, title='Advanced Lesson', sort_order=1,
            body='# Advanced\nDeep dive.',
        )
        self.preview_unit = Unit.objects.create(
            module=self.module1, title='Preview Lesson', sort_order=3,
            body='# Preview\nFree to all.',
            is_preview=True,
        )


# ============================================================
# Unit Page View Tests
# ============================================================


class CourseUnitDetailViewTest(CourseUnitSetupMixin, TestCase):
    """Test the /courses/{slug}/{module_sort}/{unit_sort} page."""

    def _login_main_user(self):
        user = User.objects.create_user(email='main@test.com', password='testpass')
        user.tier = self.main_tier
        user.save()
        self.client.login(email='main@test.com', password='testpass')
        return user

    def test_authorized_user_gets_200(self):
        self._login_main_user()
        response = self.client.get('/courses/test-course/1/1')
        self.assertEqual(response.status_code, 200)

    def test_uses_correct_template(self):
        self._login_main_user()
        response = self.client.get('/courses/test-course/1/1')
        self.assertTemplateUsed(response, 'content/course_unit_detail.html')

    def test_title_tag(self):
        self._login_main_user()
        response = self.client.get('/courses/test-course/1/1')
        self.assertContains(response, '<title>Lesson 1 | Test Course | AI Shipping Labs</title>')

    def test_shows_unit_title(self):
        self._login_main_user()
        response = self.client.get('/courses/test-course/1/1')
        self.assertContains(response, 'Lesson 1')

    def test_shows_video_player(self):
        self._login_main_user()
        response = self.client.get('/courses/test-course/1/1')
        self.assertContains(response, 'video-player')
        self.assertContains(response, 'dQw4w9WgXcB')

    def test_shows_lesson_text(self):
        self._login_main_user()
        response = self.client.get('/courses/test-course/1/1')
        self.assertContains(response, '<h1>Introduction</h1>')
        self.assertContains(response, '<strong>first</strong>')

    def test_shows_homework_section(self):
        self._login_main_user()
        response = self.client.get('/courses/test-course/1/1')
        self.assertContains(response, 'Homework')
        self.assertContains(response, '<h2>Exercise 1</h2>')
        self.assertContains(response, '<strong>this</strong>')

    def test_no_homework_section_when_empty(self):
        self._login_main_user()
        response = self.client.get('/courses/test-course/1/2')
        # The homework card with icon should not render for units without homework
        self.assertNotContains(response, 'clipboard-list')

    def test_no_video_player_when_no_video_url(self):
        self._login_main_user()
        response = self.client.get('/courses/test-course/1/2')
        self.assertNotContains(response, 'video-player')

    def test_shows_sidebar_navigation(self):
        self._login_main_user()
        response = self.client.get('/courses/test-course/1/1')
        self.assertContains(response, 'Module 1')
        self.assertContains(response, 'Module 2')
        self.assertContains(response, 'Lesson 2')
        self.assertContains(response, 'Advanced Lesson')

    def test_current_unit_highlighted_in_sidebar(self):
        self._login_main_user()
        response = self.client.get('/courses/test-course/1/1')
        content = response.content.decode()
        # The current unit should have the accent styling
        self.assertIn('bg-accent/10', content)

    def test_shows_breadcrumb(self):
        self._login_main_user()
        response = self.client.get('/courses/test-course/1/1')
        self.assertContains(response, 'Courses')
        self.assertContains(response, 'Test Course')

    def test_shows_timestamps(self):
        self._login_main_user()
        response = self.client.get('/courses/test-course/1/1')
        self.assertContains(response, 'Intro')
        self.assertContains(response, 'Setup')

    def test_nonexistent_course_returns_404(self):
        self._login_main_user()
        response = self.client.get('/courses/nonexistent/1/1')
        self.assertEqual(response.status_code, 404)

    def test_nonexistent_module_returns_404(self):
        self._login_main_user()
        response = self.client.get('/courses/test-course/99/1')
        self.assertEqual(response.status_code, 404)

    def test_nonexistent_unit_returns_404(self):
        self._login_main_user()
        response = self.client.get('/courses/test-course/1/99')
        self.assertEqual(response.status_code, 404)

    def test_draft_course_returns_404(self):
        Course.objects.create(
            title='Draft', slug='draft-course', status='draft',
        )
        self._login_main_user()
        response = self.client.get('/courses/draft-course/1/1')
        self.assertEqual(response.status_code, 404)


# ============================================================
# Access Control Tests
# ============================================================


class CourseUnitAccessControlTest(CourseUnitSetupMixin, TestCase):
    """Test access control on unit pages."""

    def test_anonymous_user_gets_403_for_non_preview(self):
        response = self.client.get('/courses/test-course/1/1')
        self.assertEqual(response.status_code, 403)

    def test_anonymous_user_sees_gated_message(self):
        response = self.client.get('/courses/test-course/1/1')
        self.assertContains(response, 'Upgrade to Main', status_code=403)
        self.assertContains(response, 'View Pricing', status_code=403)

    def test_basic_user_gets_403_for_main_course(self):
        user = User.objects.create_user(email='basic@test.com', password='testpass')
        user.tier = self.basic_tier
        user.save()
        self.client.login(email='basic@test.com', password='testpass')
        response = self.client.get('/courses/test-course/1/1')
        self.assertEqual(response.status_code, 403)

    def test_main_user_gets_200(self):
        user = User.objects.create_user(email='main@test.com', password='testpass')
        user.tier = self.main_tier
        user.save()
        self.client.login(email='main@test.com', password='testpass')
        response = self.client.get('/courses/test-course/1/1')
        self.assertEqual(response.status_code, 200)

    def test_premium_user_gets_200(self):
        user = User.objects.create_user(email='prem@test.com', password='testpass')
        user.tier = self.premium_tier
        user.save()
        self.client.login(email='prem@test.com', password='testpass')
        response = self.client.get('/courses/test-course/1/1')
        self.assertEqual(response.status_code, 200)

    def test_preview_unit_accessible_to_anonymous(self):
        response = self.client.get('/courses/test-course/1/3')
        self.assertEqual(response.status_code, 200)

    def test_preview_unit_accessible_to_basic_user(self):
        user = User.objects.create_user(email='basic@test.com', password='testpass')
        user.tier = self.basic_tier
        user.save()
        self.client.login(email='basic@test.com', password='testpass')
        response = self.client.get('/courses/test-course/1/3')
        self.assertEqual(response.status_code, 200)

    def test_preview_unit_shows_content(self):
        response = self.client.get('/courses/test-course/1/3')
        self.assertContains(response, 'Preview Lesson')
        self.assertContains(response, 'Free to all')

    def test_free_course_accessible_to_all_authenticated(self):
        free_course = Course.objects.create(
            title='Free Course', slug='free-course',
            status='published', required_level=LEVEL_OPEN, is_free=True,
        )
        module = Module.objects.create(
            course=free_course, title='M1', sort_order=1,
        )
        Unit.objects.create(
            module=module, title='Free Lesson', sort_order=1,
            body='Free content.',
        )
        user = User.objects.create_user(email='user@test.com', password='testpass')
        self.client.login(email='user@test.com', password='testpass')
        response = self.client.get('/courses/free-course/1/1')
        self.assertEqual(response.status_code, 200)

    def test_gated_page_shows_lock_icon(self):
        response = self.client.get('/courses/test-course/1/1')
        self.assertContains(response, 'lock', status_code=403)


# ============================================================
# Mark as Completed / Sidebar Checkmarks Tests
# ============================================================


class CourseUnitProgressTest(CourseUnitSetupMixin, TestCase):
    """Test mark-as-completed toggle and sidebar checkmarks."""

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(email='main@test.com', password='testpass')
        self.user.tier = self.main_tier
        self.user.save()
        self.client.login(email='main@test.com', password='testpass')

    def test_shows_mark_as_completed_button(self):
        response = self.client.get('/courses/test-course/1/1')
        self.assertContains(response, 'Mark as completed')

    def test_shows_completed_button_when_done(self):
        UserCourseProgress.objects.create(
            user=self.user, unit=self.unit1, completed_at=timezone.now(),
        )
        response = self.client.get('/courses/test-course/1/1')
        self.assertContains(response, 'Completed')

    def test_no_mark_complete_for_anonymous(self):
        self.client.logout()
        # Preview unit accessible to anonymous
        response = self.client.get('/courses/test-course/1/3')
        # The button element itself should not render for anonymous users.
        # The JS script block always has the ID ref but does nothing if button is absent.
        self.assertNotContains(response, 'id="mark-complete-btn"')

    def test_sidebar_shows_checkmark_for_completed(self):
        UserCourseProgress.objects.create(
            user=self.user, unit=self.unit1, completed_at=timezone.now(),
        )
        response = self.client.get('/courses/test-course/1/2')
        content = response.content.decode()
        # check-circle-2 icon should appear in sidebar for completed unit
        self.assertIn('check-circle-2', content)

    def test_sidebar_shows_circle_for_incomplete(self):
        response = self.client.get('/courses/test-course/1/1')
        content = response.content.decode()
        # circle icon for incomplete units
        self.assertIn('data-lucide="circle"', content)


# ============================================================
# Next Unit Button Tests
# ============================================================


class NextUnitButtonTest(CourseUnitSetupMixin, TestCase):
    """Test the next unit button navigation."""

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(email='main@test.com', password='testpass')
        self.user.tier = self.main_tier
        self.user.save()
        self.client.login(email='main@test.com', password='testpass')

    def test_next_unit_within_module(self):
        response = self.client.get('/courses/test-course/1/1')
        self.assertContains(response, 'Next: Lesson 2')
        self.assertContains(response, 'href="/courses/test-course/1/2"')

    def test_next_unit_across_module_boundary(self):
        response = self.client.get('/courses/test-course/1/3')
        # After the last unit in module 1 (sort_order 3), next is module 2 unit 1
        self.assertContains(response, 'Next: Advanced Lesson')
        self.assertContains(response, 'href="/courses/test-course/2/1"')

    def test_no_next_unit_on_last_unit(self):
        response = self.client.get('/courses/test-course/2/1')
        self.assertNotContains(response, 'Next:')

    def test_next_unit_from_first_to_second(self):
        """Verify navigation from unit 1 to unit 2 in same module."""
        response = self.client.get('/courses/test-course/1/1')
        self.assertContains(response, '/courses/test-course/1/2')


# ============================================================
# API: GET /api/courses/{slug}/units/{unit_id}
# ============================================================


class ApiCourseUnitDetailTest(CourseUnitSetupMixin, TestCase):
    """Test GET /api/courses/{slug}/units/{unit_id}."""

    def test_authorized_user_gets_200(self):
        user = User.objects.create_user(email='main@test.com', password='testpass')
        user.tier = self.main_tier
        user.save()
        self.client.login(email='main@test.com', password='testpass')
        response = self.client.get(f'/api/courses/test-course/units/{self.unit1.pk}')
        self.assertEqual(response.status_code, 200)

    def test_returns_json(self):
        user = User.objects.create_user(email='main@test.com', password='testpass')
        user.tier = self.main_tier
        user.save()
        self.client.login(email='main@test.com', password='testpass')
        response = self.client.get(f'/api/courses/test-course/units/{self.unit1.pk}')
        self.assertEqual(response['Content-Type'], 'application/json')

    def test_includes_full_content(self):
        user = User.objects.create_user(email='main@test.com', password='testpass')
        user.tier = self.main_tier
        user.save()
        self.client.login(email='main@test.com', password='testpass')
        response = self.client.get(f'/api/courses/test-course/units/{self.unit1.pk}')
        data = json.loads(response.content)
        self.assertEqual(data['title'], 'Lesson 1')
        self.assertIn('Introduction', data['body'])
        self.assertIn('Introduction', data['body_html'])
        self.assertIn('Exercise 1', data['homework'])
        self.assertEqual(data['video_url'], 'https://www.youtube.com/watch?v=dQw4w9WgXcB')
        self.assertEqual(len(data['timestamps']), 2)

    def test_includes_module_info(self):
        user = User.objects.create_user(email='main@test.com', password='testpass')
        user.tier = self.main_tier
        user.save()
        self.client.login(email='main@test.com', password='testpass')
        response = self.client.get(f'/api/courses/test-course/units/{self.unit1.pk}')
        data = json.loads(response.content)
        self.assertEqual(data['module']['title'], 'Module 1')

    def test_includes_completion_status(self):
        user = User.objects.create_user(email='main@test.com', password='testpass')
        user.tier = self.main_tier
        user.save()
        self.client.login(email='main@test.com', password='testpass')
        response = self.client.get(f'/api/courses/test-course/units/{self.unit1.pk}')
        data = json.loads(response.content)
        self.assertFalse(data['is_completed'])

    def test_completed_unit_shows_true(self):
        user = User.objects.create_user(email='main@test.com', password='testpass')
        user.tier = self.main_tier
        user.save()
        UserCourseProgress.objects.create(
            user=user, unit=self.unit1, completed_at=timezone.now(),
        )
        self.client.login(email='main@test.com', password='testpass')
        response = self.client.get(f'/api/courses/test-course/units/{self.unit1.pk}')
        data = json.loads(response.content)
        self.assertTrue(data['is_completed'])

    def test_anonymous_no_completion_field(self):
        """Anonymous access to preview unit should not include is_completed."""
        response = self.client.get(f'/api/courses/test-course/units/{self.preview_unit.pk}')
        data = json.loads(response.content)
        self.assertNotIn('is_completed', data)

    def test_unauthorized_gets_403(self):
        response = self.client.get(f'/api/courses/test-course/units/{self.unit1.pk}')
        self.assertEqual(response.status_code, 403)

    def test_unauthorized_gets_required_tier_name(self):
        response = self.client.get(f'/api/courses/test-course/units/{self.unit1.pk}')
        data = json.loads(response.content)
        self.assertEqual(data['required_tier_name'], 'Main')

    def test_preview_unit_accessible_to_anonymous(self):
        response = self.client.get(f'/api/courses/test-course/units/{self.preview_unit.pk}')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(data['title'], 'Preview Lesson')
        self.assertTrue(data['is_preview'])

    def test_preview_unit_accessible_to_basic_user(self):
        user = User.objects.create_user(email='basic@test.com', password='testpass')
        user.tier = self.basic_tier
        user.save()
        self.client.login(email='basic@test.com', password='testpass')
        response = self.client.get(f'/api/courses/test-course/units/{self.preview_unit.pk}')
        self.assertEqual(response.status_code, 200)

    def test_nonexistent_unit_returns_404(self):
        user = User.objects.create_user(email='main@test.com', password='testpass')
        user.tier = self.main_tier
        user.save()
        self.client.login(email='main@test.com', password='testpass')
        response = self.client.get('/api/courses/test-course/units/99999')
        self.assertEqual(response.status_code, 404)

    def test_unit_from_wrong_course_returns_404(self):
        """A unit ID that belongs to a different course should 404."""
        other_course = Course.objects.create(
            title='Other', slug='other-course', status='published',
        )
        other_module = Module.objects.create(
            course=other_course, title='OM', sort_order=1,
        )
        other_unit = Unit.objects.create(
            module=other_module, title='OU', sort_order=1,
        )
        response = self.client.get(f'/api/courses/test-course/units/{other_unit.pk}')
        self.assertEqual(response.status_code, 404)


# ============================================================
# API: POST /api/courses/{slug}/units/{unit_id}/complete
# ============================================================


class ApiCourseUnitCompleteTest(CourseUnitSetupMixin, TestCase):
    """Test POST /api/courses/{slug}/units/{unit_id}/complete."""

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(email='main@test.com', password='testpass')
        self.user.tier = self.main_tier
        self.user.save()
        self.client.login(email='main@test.com', password='testpass')

    def test_mark_complete_creates_progress(self):
        response = self.client.post(
            f'/api/courses/test-course/units/{self.unit1.pk}/complete',
        )
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertTrue(data['completed'])
        # Verify record exists
        self.assertTrue(
            UserCourseProgress.objects.filter(
                user=self.user, unit=self.unit1, completed_at__isnull=False,
            ).exists()
        )

    def test_toggle_off_deletes_progress(self):
        # First mark as complete
        self.client.post(
            f'/api/courses/test-course/units/{self.unit1.pk}/complete',
        )
        # Toggle off
        response = self.client.post(
            f'/api/courses/test-course/units/{self.unit1.pk}/complete',
        )
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertFalse(data['completed'])
        # Verify record deleted
        self.assertFalse(
            UserCourseProgress.objects.filter(
                user=self.user, unit=self.unit1,
            ).exists()
        )

    def test_toggle_on_again(self):
        # Complete -> uncomplete -> complete
        self.client.post(f'/api/courses/test-course/units/{self.unit1.pk}/complete')
        self.client.post(f'/api/courses/test-course/units/{self.unit1.pk}/complete')
        response = self.client.post(
            f'/api/courses/test-course/units/{self.unit1.pk}/complete',
        )
        data = json.loads(response.content)
        self.assertTrue(data['completed'])

    def test_anonymous_gets_401(self):
        self.client.logout()
        response = self.client.post(
            f'/api/courses/test-course/units/{self.unit1.pk}/complete',
        )
        self.assertEqual(response.status_code, 401)

    def test_unauthorized_user_gets_403(self):
        basic_user = User.objects.create_user(email='basic@test.com', password='testpass')
        basic_user.tier = self.basic_tier
        basic_user.save()
        self.client.login(email='basic@test.com', password='testpass')
        response = self.client.post(
            f'/api/courses/test-course/units/{self.unit1.pk}/complete',
        )
        self.assertEqual(response.status_code, 403)

    def test_get_method_not_allowed(self):
        response = self.client.get(
            f'/api/courses/test-course/units/{self.unit1.pk}/complete',
        )
        self.assertEqual(response.status_code, 405)

    def test_nonexistent_unit_returns_404(self):
        response = self.client.post(
            '/api/courses/test-course/units/99999/complete',
        )
        self.assertEqual(response.status_code, 404)

    def test_complete_preview_unit_works(self):
        """Users with access can mark preview units as complete too."""
        response = self.client.post(
            f'/api/courses/test-course/units/{self.preview_unit.pk}/complete',
        )
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertTrue(data['completed'])

    def test_nonexistent_course_returns_404(self):
        response = self.client.post(
            f'/api/courses/nonexistent/units/{self.unit1.pk}/complete',
        )
        self.assertEqual(response.status_code, 404)


# ============================================================
# Helper function tests
# ============================================================


class NextUnitHelperTest(TestCase):
    """Test the _get_next_unit helper function."""

    def setUp(self):
        self.course = Course.objects.create(
            title='Nav Course', slug='nav-course', status='published',
        )
        self.m1 = Module.objects.create(
            course=self.course, title='M1', sort_order=1,
        )
        self.m2 = Module.objects.create(
            course=self.course, title='M2', sort_order=2,
        )
        self.u1 = Unit.objects.create(module=self.m1, title='U1', sort_order=1)
        self.u2 = Unit.objects.create(module=self.m1, title='U2', sort_order=2)
        self.u3 = Unit.objects.create(module=self.m2, title='U3', sort_order=1)
        self.u4 = Unit.objects.create(module=self.m2, title='U4', sort_order=2)

    def test_next_within_module(self):
        from content.views.courses import _get_next_unit
        next_unit = _get_next_unit(self.course, self.u1)
        self.assertEqual(next_unit.pk, self.u2.pk)

    def test_next_across_module(self):
        from content.views.courses import _get_next_unit
        next_unit = _get_next_unit(self.course, self.u2)
        self.assertEqual(next_unit.pk, self.u3.pk)

    def test_last_unit_returns_none(self):
        from content.views.courses import _get_next_unit
        next_unit = _get_next_unit(self.course, self.u4)
        self.assertIsNone(next_unit)

    def test_middle_of_second_module(self):
        from content.views.courses import _get_next_unit
        next_unit = _get_next_unit(self.course, self.u3)
        self.assertEqual(next_unit.pk, self.u4.pk)
