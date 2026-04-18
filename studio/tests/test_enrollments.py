"""Tests for Studio enrollments page — issue #236."""

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


class StudioEnrollmentsAccessTest(TierSetupMixin, TestCase):

    def setUp(self):
        self.staff = User.objects.create_user(
            email='staff@example.com', password='testpass', is_staff=True,
        )
        self.user = User.objects.create_user(
            email='user@example.com', password='testpass',
        )

    def test_anonymous_redirected_to_login(self):
        response = self.client.get('/studio/enrollments/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])

    def test_non_staff_forbidden(self):
        self.client.login(email='user@example.com', password='testpass')
        response = self.client.get('/studio/enrollments/')
        self.assertEqual(response.status_code, 403)

    def test_staff_sees_page(self):
        self.client.login(email='staff@example.com', password='testpass')
        response = self.client.get('/studio/enrollments/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Enrollments')


class StudioEnrollmentsListTest(TierSetupMixin, TestCase):

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

    def test_list_shows_all_active_enrollments(self):
        response = self.client.get('/studio/enrollments/')
        self.assertContains(response, 'a@example.com')
        self.assertContains(response, 'b@example.com')
        # Three enrollment rows
        self.assertContains(response, 'data-testid="enrollment-row"', count=3)

    def test_filter_by_course(self):
        response = self.client.get(f'/studio/enrollments/?course={self.course1.pk}')
        self.assertContains(response, 'data-testid="enrollment-row"', count=2)
        self.assertContains(response, 'a@example.com')
        self.assertContains(response, 'b@example.com')

    def test_status_active_excludes_unenrolled(self):
        # Soft-delete one row
        enr = Enrollment.objects.get(user=self.user_a, course=self.course1)
        enr.unenrolled_at = timezone.now()
        enr.save(update_fields=['unenrolled_at'])
        response = self.client.get('/studio/enrollments/')
        # Default status=active filters out the unenrolled row
        self.assertContains(response, 'data-testid="enrollment-row"', count=2)

    def test_status_all_includes_unenrolled(self):
        enr = Enrollment.objects.get(user=self.user_a, course=self.course1)
        enr.unenrolled_at = timezone.now()
        enr.save(update_fields=['unenrolled_at'])
        response = self.client.get('/studio/enrollments/?status=all')
        self.assertContains(response, 'data-testid="enrollment-row"', count=3)


class StudioEnrollmentCreateTest(TierSetupMixin, TestCase):

    def setUp(self):
        self.staff = User.objects.create_user(
            email='admin@example.com', password='testpass', is_staff=True,
        )
        self.client.login(email='admin@example.com', password='testpass')
        self.user = User.objects.create_user(email='target@example.com', password='x')
        self.course = _make_course(slug='ec-create')

    def test_create_enrollment_with_admin_source(self):
        response = self.client.post('/studio/enrollments/create', {
            'email': 'target@example.com',
            'course_id': str(self.course.pk),
        })
        self.assertEqual(response.status_code, 302)
        enr = Enrollment.objects.get(user=self.user, course=self.course)
        self.assertEqual(enr.source, SOURCE_ADMIN)

    def test_create_with_unknown_email_redirects_with_error(self):
        response = self.client.post('/studio/enrollments/create', {
            'email': 'nobody@example.com',
            'course_id': str(self.course.pk),
        })
        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            Enrollment.objects.filter(course=self.course).exists(),
        )

    def test_create_idempotent_when_already_enrolled(self):
        Enrollment.objects.create(user=self.user, course=self.course)
        response = self.client.post('/studio/enrollments/create', {
            'email': 'target@example.com',
            'course_id': str(self.course.pk),
        })
        self.assertEqual(response.status_code, 302)
        # Still exactly one active enrollment
        self.assertEqual(
            Enrollment.objects.filter(user=self.user, course=self.course).count(),
            1,
        )


class StudioEnrollmentUnenrollTest(TierSetupMixin, TestCase):

    def setUp(self):
        self.staff = User.objects.create_user(
            email='admin@example.com', password='testpass', is_staff=True,
        )
        self.client.login(email='admin@example.com', password='testpass')
        self.user = User.objects.create_user(email='u@example.com', password='x')
        self.course = _make_course(slug='ec-un')
        self.enrollment = Enrollment.objects.create(user=self.user, course=self.course)

    def test_unenroll_sets_unenrolled_at(self):
        response = self.client.post(
            f'/studio/enrollments/{self.enrollment.pk}/unenroll',
        )
        self.assertEqual(response.status_code, 302)
        self.enrollment.refresh_from_db()
        self.assertIsNotNone(self.enrollment.unenrolled_at)

    def test_unenroll_already_unenrolled_is_noop(self):
        self.enrollment.unenrolled_at = timezone.now()
        self.enrollment.save(update_fields=['unenrolled_at'])
        first_ts = self.enrollment.unenrolled_at
        response = self.client.post(
            f'/studio/enrollments/{self.enrollment.pk}/unenroll',
        )
        self.assertEqual(response.status_code, 302)
        self.enrollment.refresh_from_db()
        # Timestamp not overwritten
        self.assertEqual(self.enrollment.unenrolled_at, first_ts)
