"""Every non-Maven unenroll path records the CRM row and queues the staff heads-up."""

import datetime
from unittest.mock import patch

from django.test import TestCase, tag
from django.utils import timezone

from accounts.models import Token, User
from analytics.models import UserActivity
from content.access import LEVEL_MAIN
from content.models import (
    Cohort,
    CohortEnrollment,
    Course,
    CourseAccess,
    Enrollment,
)

NOTIFY_TASK = 'community.services.staff_notifications.notify_course_unenroll'


def _queued_notifications(enqueue):
    return [call.kwargs for call in enqueue.call_args_list if call.args[0] == NOTIFY_TASK]


@tag('core')
class UnenrollNotificationPathsTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='unenroll-staff@example.com', password='testpass',
            is_staff=True, email_verified=True,
        )
        cls.learner = User.objects.create_user(
            email='unenroll-learner@example.com', password='testpass',
            email_verified=True,
        )
        cls.course = Course.objects.create(
            title='Buildcamp', slug='unenroll-buildcamp', status='published',
            required_level=0,
        )
        today = timezone.localdate()
        cls.cohort = Cohort.objects.create(
            course=cls.course, name='Cohort 4', external_key='c4',
            start_date=today - datetime.timedelta(days=7),
            end_date=today + datetime.timedelta(days=30),
        )

    def setUp(self):
        self.enrollment = Enrollment.objects.create(user=self.learner, course=self.course)
        CohortEnrollment.objects.create(cohort=self.cohort, user=self.learner)
        patcher = patch('jobs.tasks.async_task')
        self.enqueue = patcher.start()
        self.addCleanup(patcher.stop)

    def _activity(self):
        return UserActivity.objects.get(
            user=self.learner, event_type=UserActivity.EVENT_COURSE_UNENROLL,
        )

    def test_learner_self_unenroll_records_activity_and_queues_heads_up(self):
        self.client.force_login(self.learner)
        self.client.post('/courses/unenroll-buildcamp/unenroll')

        self.assertEqual(
            self._activity().label,
            'Unenrolled from course: Buildcamp, Cohort 4 (left on their own)',
        )
        self.assertEqual(_queued_notifications(self.enqueue), [{
            'user_id': self.learner.pk,
            'course_id': self.course.pk,
            'cohort_id': self.cohort.pk,
            'cause': 'self',
            'actor_id': None,
            'task_name': (
                f'Notify staff of course unenroll: user #{self.learner.pk} '
                'from unenroll-buildcamp'
            ),
        }])

    def test_learner_leaving_a_cohort_queues_heads_up_with_the_cohort(self):
        self.client.force_login(self.learner)
        response = self.client.post(
            f'/api/courses/unenroll-buildcamp/cohorts/{self.cohort.pk}/unenroll',
        )
        self.assertEqual(response.json(), {'enrolled': False, 'cohort_id': self.cohort.pk})
        [queued] = _queued_notifications(self.enqueue)
        self.assertEqual((queued['cohort_id'], queued['cause']), (self.cohort.pk, 'self'))
        self.assertIn('Cohort 4', self._activity().label)

    def test_studio_unenroll_is_attributed_to_staff(self):
        self.client.force_login(self.staff)
        self.client.post(
            f'/studio/courses/{self.course.pk}/enrollments/{self.enrollment.pk}/unenroll',
        )
        self.enrollment.refresh_from_db()
        self.assertIsNotNone(self.enrollment.unenrolled_at)
        [queued] = _queued_notifications(self.enqueue)
        self.assertEqual((queued['cause'], queued['actor_id']), ('staff', self.staff.pk))
        self.assertIn('removed by staff', self._activity().label)

    def test_api_delete_is_attributed_to_staff(self):
        token = Token.objects.create(user=self.staff, name='unenroll-test')
        response = self.client.delete(
            f'/api/courses/unenroll-buildcamp/enrollments/{self.learner.email}',
            HTTP_AUTHORIZATION=f'Token {token.key}',
        )
        self.assertEqual(response.status_code, 204)
        [queued] = _queued_notifications(self.enqueue)
        self.assertEqual((queued['cause'], queued['actor_id']), ('staff', self.staff.pk))

    def test_repeat_unenroll_does_not_notify_twice(self):
        self.client.force_login(self.learner)
        self.client.post('/courses/unenroll-buildcamp/unenroll')
        self.client.post('/courses/unenroll-buildcamp/unenroll')
        self.assertEqual(len(_queued_notifications(self.enqueue)), 1)


@tag('core')
class CourseAccessLossNotificationTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='access-staff@example.com', password='testpass',
            is_staff=True, email_verified=True,
        )
        cls.learner = User.objects.create_user(
            email='access-learner@example.com', password='testpass',
            email_verified=True,
        )
        cls.course = Course.objects.create(
            title='Paid course', slug='access-loss-course', status='published',
            required_level=LEVEL_MAIN,
        )

    def setUp(self):
        self.access = CourseAccess.objects.create(
            user=self.learner, course=self.course, access_type='granted',
        )
        patcher = patch('jobs.tasks.async_task')
        self.enqueue = patcher.start()
        self.addCleanup(patcher.stop)
        self.client.force_login(self.staff)

    def _revoke(self):
        self.client.post(
            f'/studio/courses/{self.course.pk}/access/{self.access.pk}/revoke/',
        )

    def test_revoking_an_enrolled_learners_grant_reports_access_lost(self):
        Enrollment.objects.create(user=self.learner, course=self.course)
        self._revoke()
        self.assertFalse(CourseAccess.objects.filter(pk=self.access.pk).exists())
        [queued] = _queued_notifications(self.enqueue)
        self.assertEqual((queued['cause'], queued['actor_id']), ('access_lost', self.staff.pk))

    def test_revoking_a_grant_without_an_enrollment_stays_silent(self):
        self._revoke()
        self.assertEqual(_queued_notifications(self.enqueue), [])
