"""Staff Slack heads-up posted when a learner unenrolls from a course."""

import datetime
from unittest.mock import MagicMock, patch

import requests
from django.test import TestCase, tag
from django.utils import timezone

from accounts.models import User
from community.services.staff_notifications import notify_course_unenroll
from content.models import Cohort, CohortEnrollment, Course, Enrollment

CHANNEL = 'C0STAFFOPS'


def _cfg(channel=CHANNEL):
    values = {
        'STAFF_SIGNUP_NOTIFY_CHANNEL_ID': channel,
        'SLACK_BOT_TOKEN': 'xoxb-test-token',
    }

    def _get(key, default=''):
        return values.get(key, default)

    return _get


@tag('core')
class NotifyCourseUnenrollTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.learner = User.objects.create_user(
            email='leaver@example.com', first_name='Lee', last_name='Ver',
        )
        cls.staff = User.objects.create_user(
            email='ops@example.com', is_staff=True,
        )
        cls.course = Course.objects.create(
            title='AI Engineering Buildcamp', slug='notify-buildcamp',
            status='published',
        )
        today = timezone.localdate()
        cls.cohort = Cohort.objects.create(
            course=cls.course, name='Cohort 4', external_key='c4',
            start_date=today - datetime.timedelta(days=7),
            end_date=today + datetime.timedelta(days=30),
        )
        stayer = User.objects.create_user(email='stayer@example.com')
        Enrollment.objects.create(user=stayer, course=cls.course)
        CohortEnrollment.objects.create(cohort=cls.cohort, user=stayer)

    def _notify(self, *, channel=CHANNEL, slack_enabled=True, **kwargs):
        response = MagicMock()
        response.json.return_value = {'ok': True}
        with patch(
            'community.services.staff_notifications.get_config',
            side_effect=_cfg(channel),
        ), patch(
            'community.services.staff_notifications.is_enabled',
            return_value=slack_enabled,
        ), patch(
            'community.services.staff_notifications.site_base_url',
            return_value='https://example.test',
        ), patch(
            'community.services.staff_notifications.requests.post',
            return_value=response,
        ) as post:
            delivered = notify_course_unenroll(
                self.learner.pk, self.course.pk, **kwargs,
            )
        return delivered, post

    def test_posts_learner_course_cohort_cause_and_counts_to_staff_channel(self):
        delivered, post = self._notify(
            cohort_id=self.cohort.pk, cause='staff', actor_id=self.staff.pk,
        )
        self.assertTrue(delivered)
        payload = post.call_args.kwargs['json']
        self.assertEqual(payload['channel'], CHANNEL)
        self.assertEqual(payload['text'], (
            '*Course unenroll:* Lee Ver (leaver@example.com) left '
            '`AI Engineering Buildcamp` (cohort `Cohort 4`).\n'
            '*Cause:* removed by staff (ops@example.com)\n'
            '*Active enrollments now:* 1 | *Cohort 4 members:* 1\n'
            f'*Studio:* <https://example.test/studio/users/{self.learner.pk}/|user page>'
        ))

    def test_self_unenroll_without_cohort_names_only_the_course(self):
        _delivered, post = self._notify(cause='self')
        text = post.call_args.kwargs['json']['text']
        self.assertIn('left `AI Engineering Buildcamp`.\n', text)
        self.assertIn('*Cause:* left on their own\n', text)

    def test_skips_when_channel_blank_or_slack_disabled(self):
        for kwargs in ({'channel': ''}, {'slack_enabled': False}):
            with self.subTest(**kwargs):
                delivered, post = self._notify(cause='self', **kwargs)
                self.assertFalse(delivered)
                post.assert_not_called()

    def test_slack_failure_never_raises(self):
        with patch(
            'community.services.staff_notifications.get_config',
            side_effect=_cfg(),
        ), patch(
            'community.services.staff_notifications.is_enabled',
            return_value=True,
        ), patch(
            'community.services.staff_notifications.requests.post',
            side_effect=requests.exceptions.ConnectionError('boom'),
        ):
            self.assertFalse(
                notify_course_unenroll(self.learner.pk, self.course.pk),
            )
