"""Retired request alerts and preserved proposal schedule (#1984)."""

import datetime
from unittest import mock

from django.test import TestCase, tag
from django.utils import timezone

from jobs.schedule_reconciliation import SCHEDULE_DEFINITIONS
from pods.models import Pod, PodJoinRequest
from pods.services.stale_requests import send_stale_request_alert
from pods.tests.fixtures import enroll, make_cohort, make_course, make_user


@tag('core')
class RetiredRequestAlertTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        course = make_course(slug='ai-buildcamp', title='AI Buildcamp')
        cls.cohort = make_cohort(course, key='4', name='Cohort 4')
        cls.user = make_user('student@test.com')
        cls.other_user = make_user('other-student@test.com')
        enroll(cls.user, cls.cohort)
        enroll(cls.other_user, cls.cohort)

    @mock.patch('notifications.services.staff_slack.requests.post')
    def test_old_pending_and_waitlisted_rows_never_alert_or_change(self, post):
        pod = Pod.objects.create(cohort=self.cohort, name='Historical', purpose='History', source='studio')
        rows = [
            PodJoinRequest.objects.create(pod=pod, user=user, status=status)
            for user, status in ((self.user, 'pending'), (self.other_user, 'waitlisted'))
        ]
        PodJoinRequest.objects.filter(pk__in=[row.pk for row in rows]).update(
            created_at=timezone.now() - datetime.timedelta(days=30),
        )
        self.assertEqual(send_stale_request_alert(now=timezone.now()), 0)
        post.assert_not_called()
        self.assertEqual(
            list(PodJoinRequest.objects.order_by('pk').values_list('status', 'stale_alerted_at')),
            [('pending', None), ('waitlisted', None)],
        )

    def test_shared_daily_job_remains_registered_for_stuck_proposals(self):
        definition = next(d for d in SCHEDULE_DEFINITIONS if d.name == 'pods-stale-request-alert')
        self.assertEqual(definition.cron, '0 9 * * *')
        self.assertEqual(definition.func, 'pods.tasks.stale_requests.send_stale_request_alert')
