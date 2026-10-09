"""Daily staff Slack alert for stale pod join requests (issue #1927)."""

import datetime
from unittest import mock

import requests
from django.test import TestCase, tag
from django.utils import timezone

from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting
from jobs.schedule_reconciliation import SCHEDULE_DEFINITIONS
from pods.models import POD_SOURCE_STUDIO, Pod, PodJoinRequest, PodMembership
from pods.services.stale_requests import send_stale_request_alert
from pods.tests.fixtures import enroll, make_cohort, make_course, make_user

POST_TARGET = 'notifications.services.staff_slack.requests.post'
BASE_URL = 'https://aisl.test'


def _ok():
    response = mock.Mock()
    response.json.return_value = {'ok': True}
    return response


def _payload(post_mock, index=0):
    return post_mock.call_args_list[index].kwargs['json']


def _section_texts(payload):
    return [b['text']['text'] for b in payload['blocks'] if b['type'] == 'section']


def _context_texts(payload):
    return [e['text'] for b in payload['blocks'] if b['type'] == 'context' for e in b['elements']]


class StaleAlertBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = make_course(slug='ai-buildcamp', title='AI Buildcamp')
        cls.cohort = make_cohort(cls.course, key='4', name='Cohort 4')
        cls.owner = make_user('anna@test.com', first_name='Anna', last_name='Klein')
        cls.requesters = [
            make_user(f'student{i}@test.com', first_name=f'Student{i}', last_name='Lee')
            for i in range(14)
        ]
        for user in [cls.owner, *cls.requesters]:
            enroll(user, cls.cohort)

    def setUp(self):
        self.now = timezone.now()
        for key, value in (
            ('SLACK_ENABLED', 'true'),
            ('SLACK_BOT_TOKEN', 'xoxb-test'),
            ('SITE_BASE_URL', BASE_URL),
            ('STAFF_COMMENT_NOTIFY_CHANNEL_ID', 'C_STAFF'),
            ('PODS_STALE_REQUEST_DAYS', '5'),
        ):
            self._set(key, value)
        self.addCleanup(clear_config_cache)
        patcher = mock.patch(POST_TARGET, return_value=_ok())
        self.post = patcher.start()
        self.addCleanup(patcher.stop)

    def _set(self, key, value):
        IntegrationSetting.objects.update_or_create(key=key, defaults={'value': value})
        clear_config_cache()

    def _unset(self, key):
        IntegrationSetting.objects.filter(key=key).delete()
        clear_config_cache()

    def pod(self, name='Evening builders', owner='default', max_members=4, status='open'):
        owner = self.owner if owner == 'default' else owner
        pod = Pod.objects.create(
            cohort=self.cohort, name=name, purpose='Ship', owner=owner,
            max_members=max_members, source=POD_SOURCE_STUDIO, status=status,
        )
        if owner is not None:
            PodMembership.objects.create(pod=pod, user=owner, source='staff')
        return pod

    def request(self, pod, user, days_old, status='pending'):
        join_request = PodJoinRequest.objects.create(pod=pod, user=user, status=status)
        PodJoinRequest.objects.filter(pk=join_request.pk).update(
            created_at=self.now - datetime.timedelta(days=days_old),
        )
        join_request.refresh_from_db()
        return join_request


@tag('core')
class StaleRequestAlertTest(StaleAlertBase):
    def test_posts_one_message_and_marks_every_newly_stale_request(self):
        pod = self.pod()
        first = self.request(pod, self.requesters[0], days_old=8)
        second = self.request(pod, self.requesters[1], days_old=6)

        self.assertEqual(send_stale_request_alert(now=self.now), 2)

        self.assertEqual(self.post.call_count, 1)
        payload = _payload(self.post)
        self.assertEqual(payload['channel'], 'C_STAFF')
        self.assertEqual(payload['text'], 'Stale pod requests need attention')
        self.assertEqual(
            {k: payload[k] for k in ('unfurl_links', 'link_names', 'parse')},
            {'unfurl_links': False, 'link_names': False, 'parse': 'none'},
        )
        headline, lines = _section_texts(payload)
        self.assertEqual(headline, '*2 pod requests have waited more than 5 days*')
        self.assertEqual(lines.split('\n'), [
            f'<{BASE_URL}/studio/pods/{pod.pk}/|Evening builders> (AI Buildcamp, Cohort 4) - '
            'Student0 L. (student0@test.com) asked 8 days ago - owner Anna K.',
            f'<{BASE_URL}/studio/pods/{pod.pk}/|Evening builders> (AI Buildcamp, Cohort 4) - '
            'Student1 L. (student1@test.com) asked 6 days ago - owner Anna K.',
        ])
        button = [b for b in payload['blocks'] if b['type'] == 'actions'][0]['elements'][0]
        self.assertEqual((button['text']['text'], button['url']), ('Open pods in Studio', f'{BASE_URL}/studio/pods/'))
        for join_request in (first, second):
            join_request.refresh_from_db()
            self.assertEqual(join_request.stale_alerted_at, self.now)

    def test_second_run_without_new_requests_posts_nothing(self):
        pod = self.pod()
        self.request(pod, self.requesters[0], days_old=8)
        send_stale_request_alert(now=self.now)
        self.post.reset_mock()

        self.assertEqual(send_stale_request_alert(now=self.now + datetime.timedelta(days=1)), 0)
        self.post.assert_not_called()

    def test_already_announced_requests_appear_only_in_the_earlier_line(self):
        pod = self.pod()
        for user in self.requesters[:2]:
            self.request(pod, user, days_old=9)
        send_stale_request_alert(now=self.now)
        self.post.reset_mock()
        newcomer = self.request(pod, self.requesters[2], days_old=6)

        send_stale_request_alert(now=self.now)

        payload = _payload(self.post)
        headline, lines = _section_texts(payload)
        self.assertEqual(headline, '*1 pod request has waited more than 5 days*')
        self.assertEqual(len(lines.split('\n')), 1)
        self.assertIn('student2@test.com', lines)
        self.assertNotIn('student0@test.com', lines)
        self.assertEqual(_context_texts(payload), ['2 earlier requests are still waiting.'])
        newcomer.refresh_from_db()
        self.assertIsNotNone(newcomer.stale_alerted_at)

    def test_waitlisted_archived_and_young_requests_are_never_included(self):
        full = self.pod('Full pod', max_members=1)
        self.request(full, self.requesters[0], days_old=10, status='waitlisted')
        archived = self.pod('Archived pod', status='archived')
        self.request(archived, self.requesters[1], days_old=10)
        fresh = self.pod('Fresh pod')
        self.request(fresh, self.requesters[2], days_old=4)

        self.assertEqual(send_stale_request_alert(now=self.now), 0)
        self.post.assert_not_called()
        self.assertFalse(PodJoinRequest.objects.filter(stale_alerted_at__isnull=False).exists())

    def test_caps_at_ten_lines_marks_all_and_escapes_member_text(self):
        pod = self.pod('<!channel> & friends', owner=None)
        made = [self.request(pod, user, days_old=20 - i) for i, user in enumerate(self.requesters[:13])]
        self.requesters[0].first_name = '<@U123>'
        self.requesters[0].save(update_fields=['first_name'])

        send_stale_request_alert(now=self.now)

        headline, lines = _section_texts(_payload(self.post))
        rows = lines.split('\n')
        self.assertEqual(headline, '*13 pod requests have waited more than 5 days*')
        self.assertEqual(len(rows), 11)
        self.assertEqual(rows[-1], 'and 3 more')
        self.assertIn('|&lt;!channel&gt; &amp; friends>', rows[0])
        self.assertIn('&lt;@U123&gt; L.', rows[0])
        self.assertNotIn('<!channel>', lines)
        self.assertTrue(rows[0].endswith('- owner no owner'))
        self.assertEqual(
            PodJoinRequest.objects.filter(pk__in=[r.pk for r in made], stale_alerted_at=self.now).count(), 13,
        )


@tag('core')
class StaleRequestAlertFailureTest(StaleAlertBase):
    def setUp(self):
        super().setUp()
        self.join_request = self.request(self.pod(), self.requesters[0], days_old=8)

    def _assert_nothing_marked(self):
        self.join_request.refresh_from_db()
        self.assertIsNone(self.join_request.stale_alerted_at)

    def test_slack_errors_mark_nothing_and_never_raise(self):
        rejected = mock.Mock()
        rejected.json.return_value = {'ok': False, 'error': 'channel_not_found'}
        bad_json = mock.Mock()
        bad_json.json.side_effect = ValueError('not json')
        for name, kwargs in (
            ('rejected', {'return_value': rejected}),
            ('timeout', {'side_effect': requests.exceptions.Timeout('slow')}),
            ('connection', {'side_effect': requests.exceptions.ConnectionError('down')}),
            ('bad json', {'return_value': bad_json}),
        ):
            with self.subTest(name=name):
                self.post.reset_mock(return_value=True, side_effect=True)
                self.post.configure_mock(**kwargs)
                with self.assertLogs('pods.services.stale_requests', level='ERROR'):
                    self.assertEqual(send_stale_request_alert(now=self.now), 0)
                self.assertEqual(self.post.call_count, 1)
                self._assert_nothing_marked()

    def test_failure_logs_name_the_request_count_in_singular(self):
        rejected = mock.Mock()
        rejected.json.return_value = {'ok': False, 'error': 'not_in_channel'}
        self.post.return_value = rejected
        with self.assertLogs('pods.services.stale_requests', level='ERROR') as logs:
            send_stale_request_alert(now=self.now)
        self.assertEqual(
            [r.getMessage() for r in logs.records],
            ['Stale pod request Slack alert rejected for 1 pod request (channel=C_STAFF): not_in_channel'],
        )

        self.post.reset_mock(return_value=True)
        self.post.side_effect = requests.exceptions.ConnectionError('down')
        with self.assertLogs('pods.services.stale_requests', level='ERROR') as logs:
            send_stale_request_alert(now=self.now)
        self.assertEqual(
            [r.getMessage() for r in logs.records],
            ['Stale pod request Slack alert failed for 1 pod request (channel=C_STAFF)'],
        )

    def test_unexpected_exception_is_logged_and_swallowed(self):
        with mock.patch('pods.services.stale_requests.stale_requests', side_effect=RuntimeError('db down')):
            with self.assertLogs('pods.services.stale_requests', level='ERROR'):
                self.assertEqual(send_stale_request_alert(now=self.now), 0)

    def test_missing_gates_skip_the_post_and_mark_nothing(self):
        for key, value in (
            ('SLACK_ENABLED', 'false'),
            ('SLACK_BOT_TOKEN', ''),
            ('PODS_STALE_REQUEST_ALERT_ENABLED', 'false'),
        ):
            with self.subTest(key=key):
                original = IntegrationSetting.objects.filter(key=key).first()
                self._set(key, value)
                self.assertEqual(send_stale_request_alert(now=self.now), 0)
                self.post.assert_not_called()
                self._assert_nothing_marked()
                if original is None:
                    self._unset(key)
                else:
                    self._set(key, original.value)
        # Every gate restored: the post goes out.
        self.assertEqual(send_stale_request_alert(now=self.now), 1)

    def test_missing_channel_skips_and_signup_channel_is_the_fallback(self):
        self._unset('STAFF_COMMENT_NOTIFY_CHANNEL_ID')
        self.assertEqual(send_stale_request_alert(now=self.now), 0)
        self.post.assert_not_called()
        self._assert_nothing_marked()

        self._set('STAFF_SIGNUP_NOTIFY_CHANNEL_ID', 'C_SIGNUPS')
        send_stale_request_alert(now=self.now)
        self.assertEqual(_payload(self.post)['channel'], 'C_SIGNUPS')

    def test_kill_switch_defaults_on(self):
        self._unset('PODS_STALE_REQUEST_ALERT_ENABLED')
        self.assertEqual(send_stale_request_alert(now=self.now), 1)


@tag('core')
class StaleRequestScheduleTest(TestCase):
    def test_job_is_registered_daily_at_nine_utc(self):
        definition = next(d for d in SCHEDULE_DEFINITIONS if d.name == 'pods-stale-request-alert')
        self.assertEqual(
            (definition.func, definition.cron, definition.description),
            ('pods.tasks.stale_requests.send_stale_request_alert', '0 9 * * *', 'daily at 09:00 UTC'),
        )
