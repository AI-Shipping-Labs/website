"""Leaving a course also leaves its dated cohorts, tags and cohort series."""

import datetime
from unittest.mock import patch

from django.test import TestCase, tag
from django.utils import timezone

from accounts.models import User
from accounts.utils.tags import set_tags
from content.models import Cohort, CohortEnrollment, Course, Enrollment
from content.services.course_cohorts import retract_course_and_cohort_tags
from content.services.enrollment import (
    active_enrollment_count,
    ensure_enrollment,
    unenroll,
)
from events.models import EventSeries, SeriesRegistration

NOTIFY_TASK = 'community.services.staff_notifications.notify_course_unenroll'


@tag('core')
class UnenrollLeavesCohortTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(
            title='Buildcamp', slug='leave-camp', status='published',
            required_level=0,
        )
        cls.series = EventSeries.objects.create(
            name='Office hours', slug='leave-camp-oh',
            cadence='none', day_of_week=None, start_time=None,
        )
        today = timezone.localdate()
        cls.cohort = Cohort.objects.create(
            course=cls.course, name='Cohort 4', external_key='c4',
            start_date=today - datetime.timedelta(days=7),
            end_date=today + datetime.timedelta(days=30),
            event_series=cls.series,
        )
        cls.other_learner = User.objects.create_user(
            email='stays@example.com', password='testpass',
        )
        Enrollment.objects.create(user=cls.other_learner, course=cls.course)
        CohortEnrollment.objects.create(cohort=cls.cohort, user=cls.other_learner)
        SeriesRegistration.objects.create(series=cls.series, user=cls.other_learner)

    def setUp(self):
        self.learner = User.objects.create_user(
            email='leaves@example.com', password='testpass',
        )
        set_tags(self.learner, ['newsletter', 'leave-camp', 'leave-camp-c4'])
        Enrollment.objects.create(user=self.learner, course=self.course)
        CohortEnrollment.objects.create(cohort=self.cohort, user=self.learner)
        SeriesRegistration.objects.create(series=self.series, user=self.learner)
        patcher = patch('jobs.tasks.async_task')
        self.enqueue = patcher.start()
        self.addCleanup(patcher.stop)

    def test_unenroll_removes_cohort_membership_tags_and_series(self):
        self.assertTrue(unenroll(self.learner, self.course))

        self.assertFalse(
            CohortEnrollment.objects.filter(user=self.learner).exists(),
        )
        self.assertFalse(
            SeriesRegistration.objects.filter(user=self.learner).exists(),
        )
        self.learner.refresh_from_db()
        self.assertEqual(self.learner.tags, ['newsletter'])
        # Course and cohort badges agree, and the other learner is untouched.
        self.assertEqual(active_enrollment_count(self.course), 1)
        self.assertEqual(self.cohort.enrollment_count, 1)
        self.assertTrue(
            SeriesRegistration.objects.filter(user=self.other_learner).exists(),
        )

    def test_unenroll_queues_one_notification_naming_the_cohort(self):
        unenroll(self.learner, self.course)

        queued = [
            call.kwargs for call in self.enqueue.call_args_list
            if call.args[0] == NOTIFY_TASK
        ]
        self.assertEqual(len(queued), 1)
        self.assertEqual(queued[0]['cohort_id'], self.cohort.pk)

    def test_re_enrolling_does_not_restore_the_cohort(self):
        unenroll(self.learner, self.course)

        ensure_enrollment(self.learner, self.course)

        self.assertFalse(
            CohortEnrollment.objects.filter(user=self.learner).exists(),
        )


class RetractCourseAndCohortTagsTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='tags@example.com', password='testpass',
        )

    def test_course_tag_kept_while_another_cohort_tag_remains(self):
        set_tags(self.user, ['camp', 'camp-c3', 'camp-c4'])

        removed = retract_course_and_cohort_tags(self.user, 'camp', 'camp-c4')

        self.assertEqual(removed, ['camp-c4'])
        self.user.refresh_from_db()
        self.assertEqual(self.user.tags, ['camp', 'camp-c3'])
