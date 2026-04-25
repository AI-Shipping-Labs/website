"""Tests for Studio enrollments under /studio/courses/<id>/enrollments/.

Originally introduced in #236 as a top-level Studio page; refactored in #293
to live under each course alongside ``access`` and ``peer-reviews``.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from content.models import Course, Enrollment, Module, Unit
from content.models.enrollment import (
    SOURCE_ADMIN,
    SOURCE_AUTO_PROGRESS,
    SOURCE_MANUAL,
)
from tests.fixtures import TierSetupMixin

User = get_user_model()


def _make_course(slug='c'):
    course = Course.objects.create(title=f'Course {slug}', slug=slug, status='published')
    module = Module.objects.create(course=course, title='M', slug=f'{slug}-m', sort_order=0)
    Unit.objects.create(module=module, title='U', slug=f'{slug}-u', sort_order=0)
    return course


# ---------------------------------------------------------------------------
# Authorisation
# ---------------------------------------------------------------------------

class CourseScopedEnrollmentsAccessTest(TierSetupMixin, TestCase):
    """The course-scoped page enforces login + staff."""

    def setUp(self):
        self.staff = User.objects.create_user(
            email='staff@example.com', password='testpass', is_staff=True,
        )
        self.user = User.objects.create_user(
            email='user@example.com', password='testpass',
        )
        self.course = _make_course(slug='c-acl')

    def test_anonymous_redirected_to_login(self):
        response = self.client.get(
            f'/studio/courses/{self.course.pk}/enrollments/',
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])

    def test_non_staff_forbidden_on_list(self):
        self.client.login(email='user@example.com', password='testpass')
        response = self.client.get(
            f'/studio/courses/{self.course.pk}/enrollments/',
        )
        self.assertEqual(response.status_code, 403)

    def test_non_staff_forbidden_on_create(self):
        self.client.login(email='user@example.com', password='testpass')
        response = self.client.post(
            f'/studio/courses/{self.course.pk}/enrollments/create',
            {'email': 'user@example.com'},
        )
        self.assertEqual(response.status_code, 403)

    def test_non_staff_forbidden_on_unenroll(self):
        enrollment = Enrollment.objects.create(
            user=self.user, course=self.course,
        )
        self.client.login(email='user@example.com', password='testpass')
        response = self.client.post(
            f'/studio/courses/{self.course.pk}/enrollments/{enrollment.pk}/unenroll',
        )
        self.assertEqual(response.status_code, 403)

    def test_staff_sees_page(self):
        self.client.login(email='staff@example.com', password='testpass')
        response = self.client.get(
            f'/studio/courses/{self.course.pk}/enrollments/',
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'studio/courses/enrollments_list.html')


# ---------------------------------------------------------------------------
# Listing — scoped to one course, no leakage
# ---------------------------------------------------------------------------

class CourseScopedEnrollmentsListTest(TierSetupMixin, TestCase):

    def setUp(self):
        self.staff = User.objects.create_user(
            email='admin@example.com', password='testpass', is_staff=True,
        )
        self.client.login(email='admin@example.com', password='testpass')

        self.user_a = User.objects.create_user(email='a@example.com', password='x')
        self.user_b = User.objects.create_user(email='b@example.com', password='x')
        self.course1 = _make_course(slug='ec1')
        self.course2 = _make_course(slug='ec2')

        Enrollment.objects.create(user=self.user_a, course=self.course1, source=SOURCE_MANUAL)
        Enrollment.objects.create(user=self.user_b, course=self.course1, source=SOURCE_AUTO_PROGRESS)
        Enrollment.objects.create(user=self.user_a, course=self.course2, source=SOURCE_ADMIN)

    def test_list_shows_only_this_courses_enrollments(self):
        response = self.client.get(
            f'/studio/courses/{self.course1.pk}/enrollments/',
        )
        self.assertEqual(response.status_code, 200)
        # Two rows on course1, none from course2 leak through
        self.assertContains(response, 'data-testid="enrollment-row"', count=2)
        self.assertContains(response, 'a@example.com')
        self.assertContains(response, 'b@example.com')

    def test_other_course_only_shows_its_own_enrollment(self):
        response = self.client.get(
            f'/studio/courses/{self.course2.pk}/enrollments/',
        )
        self.assertContains(response, 'data-testid="enrollment-row"', count=1)
        self.assertContains(response, 'a@example.com')
        self.assertNotContains(response, 'b@example.com')

    def test_unknown_course_returns_404(self):
        response = self.client.get('/studio/courses/999999/enrollments/')
        self.assertEqual(response.status_code, 404)

    def test_status_active_excludes_unenrolled(self):
        enr = Enrollment.objects.get(user=self.user_a, course=self.course1)
        enr.unenrolled_at = timezone.now()
        enr.save(update_fields=['unenrolled_at'])
        response = self.client.get(
            f'/studio/courses/{self.course1.pk}/enrollments/',
        )
        self.assertContains(response, 'data-testid="enrollment-row"', count=1)

    def test_status_all_includes_unenrolled(self):
        enr = Enrollment.objects.get(user=self.user_a, course=self.course1)
        enr.unenrolled_at = timezone.now()
        enr.save(update_fields=['unenrolled_at'])
        response = self.client.get(
            f'/studio/courses/{self.course1.pk}/enrollments/?status=all',
        )
        self.assertContains(response, 'data-testid="enrollment-row"', count=2)

    def test_page_does_not_render_a_course_dropdown(self):
        response = self.client.get(
            f'/studio/courses/{self.course1.pk}/enrollments/',
        )
        # The old global page had <select name="course">; the scoped page
        # must not. The enroll form also no longer has a course_id select.
        self.assertNotContains(response, 'name="course"')
        self.assertNotContains(response, 'name="course_id"')

    def test_breadcrumbs_link_to_courses_and_course_edit(self):
        response = self.client.get(
            f'/studio/courses/{self.course1.pk}/enrollments/',
        )
        self.assertContains(response, 'href="/studio/courses/"')
        self.assertContains(
            response, f'href="/studio/courses/{self.course1.pk}/edit"',
        )

    def test_empty_state_text(self):
        empty_course = _make_course(slug='ec-empty')
        response = self.client.get(
            f'/studio/courses/{empty_course.pk}/enrollments/',
        )
        self.assertContains(response, 'No enrollments for this course yet.')


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

class CourseScopedEnrollmentCreateTest(TierSetupMixin, TestCase):

    def setUp(self):
        self.staff = User.objects.create_user(
            email='admin@example.com', password='testpass', is_staff=True,
        )
        self.client.login(email='admin@example.com', password='testpass')
        self.user = User.objects.create_user(email='target@example.com', password='x')
        self.course = _make_course(slug='ec-create')

    def test_create_enrollment_with_admin_source(self):
        response = self.client.post(
            f'/studio/courses/{self.course.pk}/enrollments/create',
            {'email': 'target@example.com'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response['Location'],
            f'/studio/courses/{self.course.pk}/enrollments/',
        )
        enr = Enrollment.objects.get(user=self.user, course=self.course)
        self.assertEqual(enr.source, SOURCE_ADMIN)

    def test_create_with_unknown_email_redirects_with_error(self):
        response = self.client.post(
            f'/studio/courses/{self.course.pk}/enrollments/create',
            {'email': 'nobody@example.com'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            Enrollment.objects.filter(course=self.course).exists(),
        )

    def test_create_without_email_redirects_with_error(self):
        response = self.client.post(
            f'/studio/courses/{self.course.pk}/enrollments/create',
            {'email': ''},
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            Enrollment.objects.filter(course=self.course).exists(),
        )

    def test_create_idempotent_when_already_enrolled(self):
        Enrollment.objects.create(user=self.user, course=self.course)
        response = self.client.post(
            f'/studio/courses/{self.course.pk}/enrollments/create',
            {'email': 'target@example.com'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            Enrollment.objects.filter(user=self.user, course=self.course).count(),
            1,
        )

    def test_create_404_for_unknown_course(self):
        response = self.client.post(
            '/studio/courses/999999/enrollments/create',
            {'email': 'target@example.com'},
        )
        self.assertEqual(response.status_code, 404)


# ---------------------------------------------------------------------------
# Unenroll — including cross-course safety
# ---------------------------------------------------------------------------

class CourseScopedEnrollmentUnenrollTest(TierSetupMixin, TestCase):

    def setUp(self):
        self.staff = User.objects.create_user(
            email='admin@example.com', password='testpass', is_staff=True,
        )
        self.client.login(email='admin@example.com', password='testpass')
        self.user = User.objects.create_user(email='u@example.com', password='x')
        self.course = _make_course(slug='ec-un')
        self.other_course = _make_course(slug='ec-un-other')
        self.enrollment = Enrollment.objects.create(user=self.user, course=self.course)

    def test_unenroll_sets_unenrolled_at_and_redirects(self):
        response = self.client.post(
            f'/studio/courses/{self.course.pk}/enrollments/{self.enrollment.pk}/unenroll',
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response['Location'],
            f'/studio/courses/{self.course.pk}/enrollments/',
        )
        self.enrollment.refresh_from_db()
        self.assertIsNotNone(self.enrollment.unenrolled_at)

    def test_unenroll_already_unenrolled_is_noop(self):
        self.enrollment.unenrolled_at = timezone.now()
        self.enrollment.save(update_fields=['unenrolled_at'])
        first_ts = self.enrollment.unenrolled_at
        response = self.client.post(
            f'/studio/courses/{self.course.pk}/enrollments/{self.enrollment.pk}/unenroll',
        )
        self.assertEqual(response.status_code, 302)
        self.enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.unenrolled_at, first_ts)

    def test_unenroll_under_wrong_course_returns_404(self):
        response = self.client.post(
            f'/studio/courses/{self.other_course.pk}/enrollments/{self.enrollment.pk}/unenroll',
        )
        self.assertEqual(response.status_code, 404)
        self.enrollment.refresh_from_db()
        self.assertIsNone(self.enrollment.unenrolled_at)


# ---------------------------------------------------------------------------
# Legacy redirect shims
# ---------------------------------------------------------------------------

class LegacyEnrollmentsRedirectTest(TierSetupMixin, TestCase):
    """Old ``/studio/enrollments/...`` URLs are redirected to the new ones."""

    def setUp(self):
        self.staff = User.objects.create_user(
            email='admin@example.com', password='testpass', is_staff=True,
        )
        self.client.login(email='admin@example.com', password='testpass')
        self.user = User.objects.create_user(email='target@example.com', password='x')
        self.course = _make_course(slug='legacy-c')
        self.enrollment = Enrollment.objects.create(
            user=self.user, course=self.course,
        )

    # GET /studio/enrollments/

    def test_list_with_course_redirects_301_to_scoped_page(self):
        response = self.client.get(
            f'/studio/enrollments/?course={self.course.pk}',
        )
        self.assertEqual(response.status_code, 301)
        self.assertEqual(
            response['Location'],
            f'/studio/courses/{self.course.pk}/enrollments/',
        )

    def test_list_with_course_and_status_preserves_status(self):
        response = self.client.get(
            f'/studio/enrollments/?course={self.course.pk}&status=all',
        )
        self.assertEqual(response.status_code, 301)
        self.assertEqual(
            response['Location'],
            f'/studio/courses/{self.course.pk}/enrollments/?status=all',
        )

    def test_list_with_no_course_redirects_to_course_list(self):
        response = self.client.get('/studio/enrollments/')
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], '/studio/courses/')

    def test_list_with_invalid_course_redirects_to_course_list(self):
        response = self.client.get('/studio/enrollments/?course=not-a-number')
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], '/studio/courses/')

    def test_list_with_unknown_course_redirects_to_course_list(self):
        response = self.client.get('/studio/enrollments/?course=999999')
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], '/studio/courses/')

    def test_list_redirect_followed_lands_on_scoped_page(self):
        response = self.client.get(
            f'/studio/enrollments/?course={self.course.pk}', follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'studio/courses/enrollments_list.html')

    def test_list_redirect_followed_with_no_course_lands_on_course_list(self):
        response = self.client.get('/studio/enrollments/', follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'studio/courses/list.html')

    # POST /studio/enrollments/create

    def test_create_redirects_307_with_valid_course_id(self):
        response = self.client.post(
            '/studio/enrollments/create',
            {'email': 'target@example.com', 'course_id': str(self.course.pk)},
        )
        self.assertEqual(response.status_code, 307)
        self.assertEqual(
            response['Location'],
            f'/studio/courses/{self.course.pk}/enrollments/create',
        )

    def test_create_redirect_followed_actually_enrolls_user(self):
        # 307 preserves method+body, so the POST should re-hit the new
        # create endpoint with the original email and create the enrollment.
        Enrollment.objects.filter(user=self.user, course=self.course).delete()
        response = self.client.post(
            '/studio/enrollments/create',
            {'email': 'target@example.com', 'course_id': str(self.course.pk)},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            Enrollment.objects.filter(
                user=self.user, course=self.course, source=SOURCE_ADMIN,
            ).exists(),
        )

    def test_create_without_course_id_falls_back_to_course_list(self):
        response = self.client.post(
            '/studio/enrollments/create',
            {'email': 'target@example.com'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/studio/courses/')

    def test_create_with_unknown_course_id_falls_back_to_course_list(self):
        response = self.client.post(
            '/studio/enrollments/create',
            {'email': 'target@example.com', 'course_id': '999999'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/studio/courses/')

    # POST /studio/enrollments/<id>/unenroll

    def test_unenroll_redirects_307_to_scoped_unenroll(self):
        response = self.client.post(
            f'/studio/enrollments/{self.enrollment.pk}/unenroll',
        )
        self.assertEqual(response.status_code, 307)
        self.assertEqual(
            response['Location'],
            f'/studio/courses/{self.course.pk}/enrollments/{self.enrollment.pk}/unenroll',
        )

    def test_unenroll_redirect_followed_actually_unenrolls(self):
        response = self.client.post(
            f'/studio/enrollments/{self.enrollment.pk}/unenroll', follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.enrollment.refresh_from_db()
        self.assertIsNotNone(self.enrollment.unenrolled_at)

    def test_unenroll_unknown_id_falls_back_to_course_list(self):
        response = self.client.post('/studio/enrollments/999999/unenroll')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/studio/courses/')


# ---------------------------------------------------------------------------
# Sidebar + entry points
# ---------------------------------------------------------------------------

class SidebarAndEntryPointsTest(TierSetupMixin, TestCase):
    """The Studio sidebar and course-edit page advertise the new path."""

    def setUp(self):
        self.staff = User.objects.create_user(
            email='admin@example.com', password='testpass', is_staff=True,
        )
        self.client.login(email='admin@example.com', password='testpass')
        self.course = _make_course(slug='c-sidebar')

    def test_sidebar_does_not_have_top_level_enrollments_link(self):
        response = self.client.get('/studio/')
        # The old <a> linked to /studio/enrollments/ with text 'Enrollments'.
        self.assertNotContains(response, 'href="/studio/enrollments/"')

    def test_course_edit_page_has_manage_enrollments_button(self):
        response = self.client.get(
            f'/studio/courses/{self.course.pk}/edit',
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            f'href="/studio/courses/{self.course.pk}/enrollments/"',
        )
        self.assertContains(response, 'Manage Enrollments')


# ---------------------------------------------------------------------------
# Cleanup checks — old URL names no longer reverse
# ---------------------------------------------------------------------------

class LegacyUrlNamesRemovedTest(TestCase):
    """The old URL names are gone; reverse() must raise NoReverseMatch."""

    def test_studio_enrollment_list_name_unregistered(self):
        from django.urls import NoReverseMatch, reverse
        with self.assertRaises(NoReverseMatch):
            reverse('studio_enrollment_list')

    def test_studio_enrollment_create_name_unregistered(self):
        from django.urls import NoReverseMatch, reverse
        with self.assertRaises(NoReverseMatch):
            reverse('studio_enrollment_create')

    def test_studio_enrollment_unenroll_name_unregistered(self):
        from django.urls import NoReverseMatch, reverse
        with self.assertRaises(NoReverseMatch):
            reverse('studio_enrollment_unenroll', args=[1])
