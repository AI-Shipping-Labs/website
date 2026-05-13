"""Tests for Studio user-detail course enrollment context (issue #591)."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from content.models import Course, Enrollment, Module, Unit, UserCourseProgress
from studio.views.users import _build_course_enrollments

User = get_user_model()


class _CourseContextBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff-591@test.com', password='pw', is_staff=True,
        )
        cls.member = User.objects.create_user(
            email='member-591@test.com', password='pw',
        )

    def setUp(self):
        self.client.login(email='staff-591@test.com', password='pw')

    def _course_with_units(self, slug, title, unit_count=2):
        course = Course.objects.create(
            title=title,
            slug=slug,
            status='published',
        )
        module = Module.objects.create(
            course=course,
            title=f'{title} Module',
            slug=f'{slug}-module',
            sort_order=1,
        )
        units = [
            Unit.objects.create(
                module=module,
                title=f'{title} Unit {index}',
                slug=f'{slug}-unit-{index}',
                sort_order=index,
            )
            for index in range(1, unit_count + 1)
        ]
        return course, units

    def _enroll(self, course, enrolled_at, unenrolled_at=None):
        enrollment = Enrollment.objects.create(
            user=self.member,
            course=course,
            unenrolled_at=unenrolled_at,
        )
        Enrollment.objects.filter(pk=enrollment.pk).update(
            enrolled_at=enrolled_at,
        )
        enrollment.refresh_from_db()
        return enrollment

    def _complete(self, unit, completed_at):
        return UserCourseProgress.objects.create(
            user=self.member,
            unit=unit,
            completed_at=completed_at,
        )


class CourseEnrollmentRollupHelperTest(_CourseContextBase):
    def test_builds_status_dates_and_ordering_with_constant_queries(self):
        now = timezone.now()
        completed_course, completed_units = self._course_with_units(
            'completed-course', 'Completed Course', unit_count=2,
        )
        active_course, active_units = self._course_with_units(
            'active-course', 'Active Course', unit_count=2,
        )
        dropped_course, dropped_units = self._course_with_units(
            'dropped-course', 'Dropped Course', unit_count=1,
        )

        self._enroll(completed_course, now - timedelta(days=30))
        self._enroll(active_course, now - timedelta(days=10))
        self._enroll(
            dropped_course,
            now - timedelta(days=5),
            unenrolled_at=now - timedelta(days=2),
        )

        self._complete(completed_units[0], now - timedelta(days=4))
        self._complete(completed_units[1], now - timedelta(days=1))
        self._complete(active_units[0], now - timedelta(days=3))
        self._complete(dropped_units[0], now)

        with self.assertNumQueries(2):
            rows = _build_course_enrollments(self.member)

        self.assertEqual(
            [row['course_slug'] for row in rows],
            ['dropped-course', 'completed-course', 'active-course'],
        )
        rows_by_slug = {row['course_slug']: row for row in rows}
        self.assertEqual(rows_by_slug['completed-course']['status'], 'Completed')
        self.assertEqual(rows_by_slug['active-course']['status'], 'Enrolled')
        self.assertEqual(rows_by_slug['dropped-course']['status'], 'Dropped')
        self.assertEqual(
            rows_by_slug['completed-course']['last_activity_at'].date(),
            (now - timedelta(days=1)).date(),
        )
        self.assertEqual(
            rows_by_slug['active-course']['last_activity_at'].date(),
            (now - timedelta(days=3)).date(),
        )
        self.assertEqual(
            rows_by_slug['dropped-course']['course_url'],
            reverse('studio_course_edit', args=[dropped_course.pk]),
        )

    def test_active_course_with_no_completed_units_uses_enrolled_date(self):
        now = timezone.now()
        course, _units = self._course_with_units(
            'fresh-course', 'Fresh Course', unit_count=1,
        )
        enrollment = self._enroll(course, now - timedelta(days=2))

        rows = _build_course_enrollments(self.member)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['status'], 'Enrolled')
        self.assertEqual(rows[0]['last_activity_at'], enrollment.enrolled_at)

    def test_empty_course_is_not_marked_completed(self):
        now = timezone.now()
        course, _units = self._course_with_units(
            'empty-course', 'Empty Course', unit_count=0,
        )
        self._enroll(course, now)

        rows = _build_course_enrollments(self.member)

        self.assertEqual(rows[0]['status'], 'Enrolled')


class UserDetailCourseContextViewTest(_CourseContextBase):
    def test_staff_page_renders_course_context_table(self):
        now = timezone.now()
        completed_course, completed_units = self._course_with_units(
            'rendered-completed', 'Rendered Completed', unit_count=1,
        )
        active_course, _active_units = self._course_with_units(
            'rendered-active', 'Rendered Active', unit_count=1,
        )
        dropped_course, _dropped_units = self._course_with_units(
            'rendered-dropped', 'Rendered Dropped', unit_count=1,
        )
        completed_enrollment = self._enroll(
            completed_course, now - timedelta(days=8),
        )
        active_enrollment = self._enroll(
            active_course, now - timedelta(days=3),
        )
        dropped_enrollment = self._enroll(
            dropped_course,
            now - timedelta(days=6),
            unenrolled_at=now - timedelta(days=1),
        )
        self._complete(completed_units[0], now - timedelta(days=1))

        response = self.client.get(
            reverse('studio_user_detail', args=[self.member.pk]),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, 'data-testid="user-detail-course-context-section"',
        )
        self.assertContains(response, '<h2', html=False)
        self.assertContains(response, 'Course context')
        self.assertContains(
            response, 'data-testid="user-detail-course-context-row"', count=3,
        )
        self.assertContains(response, 'data-course-slug="rendered-completed"')
        self.assertContains(response, 'data-course-slug="rendered-active"')
        self.assertContains(response, 'data-course-slug="rendered-dropped"')
        self.assertContains(response, 'Rendered Completed')
        self.assertContains(
            response, reverse('studio_course_edit', args=[completed_course.pk]),
        )
        self.assertContains(response, 'Completed')
        self.assertContains(response, 'Enrolled')
        self.assertContains(response, 'Dropped')
        self.assertContains(
            response, 'bg-green-500/10 text-green-500 border border-green-500/30',
        )
        self.assertContains(
            response, 'bg-accent/10 text-accent border border-accent/30',
        )
        self.assertContains(
            response, 'bg-muted text-muted-foreground border border-border',
        )
        self.assertContains(
            response, completed_enrollment.enrolled_at.strftime('%Y-%m-%d'),
        )
        self.assertContains(
            response, active_enrollment.enrolled_at.strftime('%Y-%m-%d'),
        )
        self.assertContains(
            response, dropped_enrollment.enrolled_at.strftime('%Y-%m-%d'),
        )

    def test_empty_state_renders_without_table_rows(self):
        response = self.client.get(
            reverse('studio_user_detail', args=[self.member.pk]),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, 'data-testid="user-detail-course-context-empty"',
        )
        self.assertContains(response, 'No course enrollments yet.')
        self.assertNotContains(
            response, 'data-testid="user-detail-course-context-row"',
        )

    def test_course_context_sits_between_tags_and_crm(self):
        response = self.client.get(
            reverse('studio_user_detail', args=[self.member.pk]),
        )
        body = response.content.decode()

        tags_idx = body.index('data-testid="user-tags-section"')
        course_context_idx = body.index(
            'data-testid="user-detail-course-context-section"',
        )
        crm_idx = body.index('data-testid="user-crm-section"')

        self.assertLess(tags_idx, course_context_idx)
        self.assertLess(course_context_idx, crm_idx)

    def test_anonymous_user_is_redirected_to_login(self):
        self.client.logout()

        response = self.client.get(
            reverse('studio_user_detail', args=[self.member.pk]),
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])
