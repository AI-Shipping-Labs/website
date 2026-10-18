"""Stuck pod meeting proposals (issue #1935): the rule, the daily staff
Slack section, Studio ``Stuck``/``No answer`` details and the staff API.

The clock is frozen (date-rot-ok: fixed Friday 2026-10-09 08:00 UTC).
"""

import datetime
import json
import uuid
from datetime import UTC
from unittest import mock

from django.test import TestCase, tag
from freezegun import freeze_time

from accounts.models import Token
from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting
from pods.models import (
    MEETING_RESPONSE_CANT,
    MEETING_RESPONSE_GOING,
    PodMeeting,
    PodMeetingResponse,
)
from pods.services import config as pods_config
from pods.services import meetings as mtg
from pods.services.stale_requests import send_stale_request_alert
from pods.services.stuck_proposals import stuck_proposals
from pods.tests.fixtures import enroll, make_cohort, make_course, make_meeting, make_pod, make_user
from pods.tests.test_meeting_views import texts

NOW = datetime.datetime(2026, 10, 9, 8, 0, tzinfo=UTC)  # date-rot-ok: frozen Friday
POST_TARGET = 'notifications.services.staff_slack.requests.post'
BASE_URL = 'https://aisl.test'
HOUR = datetime.timedelta(hours=1)
DAY = datetime.timedelta(days=1)


def _ok():
    response = mock.Mock()
    response.json.return_value = {'ok': True}
    return response


def _rejected(error='channel_not_found'):
    response = mock.Mock()
    response.json.return_value = {'ok': False, 'error': error}
    return response


def _payload(post_mock):
    return post_mock.call_args.kwargs['json']


def _section_texts(payload):
    return [b['text']['text'] for b in payload['blocks'] if b['type'] == 'section']


def _context_texts(payload):
    return [e['text'] for b in payload['blocks'] if b['type'] == 'context' for e in b['elements']]


class StuckFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = make_course(slug='ai-buildcamp', title='AI Buildcamp')
        cls.cohort = make_cohort(cls.course, key='4', name='Cohort 4')
        cls.anna = make_user('anna@example.com', first_name='Anna', last_name='Klein')
        cls.mike = make_user('mike@example.com', first_name='Mike', last_name='Kowalski')
        cls.raj = make_user('raj@example.com', first_name='Raj', last_name='Patel')
        cls.staff = make_user('staff@example.com', staff=True)
        for user in (cls.anna, cls.mike, cls.raj):
            enroll(user, cls.cohort)
        type(cls.mike).objects.filter(pk=cls.mike.pk).update(last_login=NOW - 12 * DAY)
        type(cls.anna).objects.filter(pk=cls.anna.pk).update(last_login=NOW - 2 * HOUR)

    def setUp(self):
        for key, value in (
            ('SLACK_ENABLED', 'true'),
            ('SLACK_BOT_TOKEN', 'xoxb-test'),
            ('SITE_BASE_URL', BASE_URL),
            ('STAFF_COMMENT_NOTIFY_CHANNEL_ID', 'C_STAFF'),
        ):
            self._set(key, value)
        self.addCleanup(clear_config_cache)
        patcher = mock.patch(POST_TARGET, return_value=_ok())
        self.post = patcher.start()
        self.addCleanup(patcher.stop)

    def _set(self, key, value):
        IntegrationSetting.objects.update_or_create(key=key, defaults={'value': value})
        clear_config_cache()

    def pod(self, name='Evening builders', members=None, **extra):
        members = [self.anna, self.mike] if members is None else members
        return make_pod(self.cohort, name, members, owner=members[0] if members else None, **extra)

    def proposal(self, pod, *, age, starts_in=4 * DAY, by=None, series=0, status='proposed'):
        """A proposal by ``by`` (Anna) created ``age`` ago; ``series`` > 0 makes a weekly one."""
        by = by or self.anna
        series_id = uuid.uuid4() if series else None
        meetings = [
            make_meeting(
                pod, NOW + starts_in + k * 7 * DAY, status=status, series_id=series_id,
                created_via='member', proposed_by=by,
            )
            for k in range(max(series, 1))
        ]
        PodMeeting.objects.filter(pk__in=[m.pk for m in meetings]).update(created_at=NOW - age)
        if status == 'proposed':
            PodMeetingResponse.objects.create(meeting=meetings[0], user=by, response=MEETING_RESPONSE_GOING)
        for meeting in meetings:
            meeting.refresh_from_db()
        return meetings[0] if not series else meetings


# --- The rule ---------------------------------------------------------------------------

@tag('core')
@freeze_time(NOW)
class StuckRuleTest(StuckFixture):
    def stuck_ids(self):
        return [wait.head.pk for wait in stuck_proposals(now=NOW)]

    def test_threshold_is_strictly_more_than_the_setting(self):
        old = self.proposal(self.pod('Old'), age=49 * HOUR)
        self.proposal(self.pod('Young'), age=47 * HOUR)
        self.assertEqual(self.stuck_ids(), [old.pk])

        self._set('PODS_STUCK_PROPOSAL_HOURS', '50')
        self.assertEqual(self.stuck_ids(), [])
        self._set('PODS_STUCK_PROPOSAL_HOURS', '24')
        self.assertEqual(len(self.stuck_ids()), 2)

    def test_default_boundary_is_pinned_at_exactly_48_hours(self):
        second = datetime.timedelta(seconds=1)
        at_48h = self.proposal(self.pod('Exactly 48h'), age=48 * HOUR)
        just_over = self.proposal(self.pod('48h plus 1s'), age=48 * HOUR + second)
        just_under = self.proposal(self.pod('47h59m'), age=47 * HOUR + 59 * datetime.timedelta(minutes=1))
        stuck = self.stuck_ids()
        self.assertEqual(stuck, [just_over.pk])
        self.assertNotIn(at_48h.pk, stuck)
        self.assertNotIn(just_under.pk, stuck)

    def test_setting_boundary_is_pinned_at_exactly_24_hours(self):
        self._set('PODS_STUCK_PROPOSAL_HOURS', '24')
        second = datetime.timedelta(seconds=1)
        self.proposal(self.pod('Exactly 24h'), age=24 * HOUR)
        just_over = self.proposal(self.pod('24h plus 1s'), age=24 * HOUR + second)
        self.proposal(self.pod('23h59m'), age=23 * HOUR + 59 * datetime.timedelta(minutes=1))
        self.assertEqual(self.stuck_ids(), [just_over.pk])

    def test_hours_setting_falls_back_to_48_when_invalid(self):
        for raw in ('0', '337', 'soon', ''):
            with self.subTest(raw=raw):
                self._set('PODS_STUCK_PROPOSAL_HOURS', raw)
                self.assertEqual(pods_config.stuck_proposal_hours(), 48)
        self._set('PODS_STUCK_PROPOSAL_HOURS', '336')
        self.assertEqual(pods_config.stuck_proposal_hours(), 336)

    def test_expired_scheduled_cancelled_series_tail_and_archived_are_never_stuck(self):
        self.proposal(self.pod('Expired'), age=5 * DAY, starts_in=-HOUR)
        # A weekly series whose first meeting passed is expired as a whole,
        # even though its later meetings are still in the future.
        self.proposal(self.pod('Expired series'), age=5 * DAY, starts_in=-HOUR, series=3)
        self.proposal(self.pod('Scheduled'), age=5 * DAY, status='scheduled')
        self.proposal(self.pod('Cancelled'), age=5 * DAY, status='cancelled')
        self.proposal(self.pod('Archived', status='archived'), age=5 * DAY)
        head, *tail = self.proposal(self.pod('Live series'), age=5 * DAY, series=3)

        self.assertEqual(self.stuck_ids(), [head.pk])
        self.assertNotIn(tail[0].pk, self.stuck_ids())

    def test_waiting_on_is_unanswered_members_and_cant_is_separate(self):
        pod = self.pod(members=[self.anna, self.mike, self.raj])
        head = self.proposal(pod, age=3 * DAY)
        PodMeetingResponse.objects.create(meeting=head, user=self.raj, response=MEETING_RESPONSE_CANT)
        [wait] = stuck_proposals(now=NOW)
        self.assertEqual((wait.waiting, wait.cant), ([self.mike], [self.raj]))

    def test_moving_clears_the_alert_mark_and_restarts_the_clock(self):
        head = self.proposal(self.pod(), age=3 * DAY)
        PodMeeting.objects.filter(pk=head.pk).update(stuck_alerted_at=NOW - DAY)
        head.refresh_from_db()

        mtg.move_meeting(head, self.anna, NOW + 5 * DAY, now=NOW)

        head.refresh_from_db()
        self.assertIsNone(head.stuck_alerted_at)
        self.assertEqual(head.moved_at, NOW)
        self.assertEqual(self.stuck_ids(), [])
        with freeze_time(NOW + 49 * HOUR):
            self.assertEqual([w.head.pk for w in stuck_proposals(now=NOW + 49 * HOUR)], [head.pk])


# --- The daily Slack alert -------------------------------------------------------------

@tag('core')
@freeze_time(NOW)
class StuckProposalAlertTest(StuckFixture):
    def test_posts_proposals_section_alone_and_marks_only_the_head(self):
        pod = self.pod()
        head = self.proposal(pod, age=3 * DAY)
        type(self.mike).objects.filter(pk=self.mike.pk).update(last_login=None)

        self.assertEqual(send_stale_request_alert(now=NOW), 1)

        payload = _payload(self.post)
        self.assertEqual(payload['channel'], 'C_STAFF')
        self.assertEqual(payload['text'], 'Stuck pod proposals need attention')
        self.assertEqual(_section_texts(payload), [
            '*1 pod proposal has waited more than 48 hours or half the time to the meeting*',
            f'<{BASE_URL}/studio/pods/{pod.pk}/|Evening builders> (AI Buildcamp, Cohort 4) - '
            'Tue Oct 13, 08:00 UTC, proposed by Anna K. 3 days ago - '
            'waiting on Mike K. (mike@example.com, never signed in)',
        ])
        self.assertEqual(_context_texts(payload), [])
        button = [b for b in payload['blocks'] if b['type'] == 'actions'][0]['elements'][0]
        self.assertEqual((button['text']['text'], button['url']), ('Open pods in Studio', f'{BASE_URL}/studio/pods/'))
        head.refresh_from_db()
        self.assertEqual(head.stuck_alerted_at, NOW)

    def test_weekly_series_is_one_line_and_only_its_head_is_marked(self):
        head, *tail = self.proposal(self.pod(), age=3 * DAY, series=3)
        send_stale_request_alert(now=NOW)
        self.assertEqual(len(_section_texts(_payload(self.post))[1].split('\n')), 1)
        self.assertEqual(
            list(PodMeeting.objects.filter(stuck_alerted_at__isnull=False).values_list('pk', flat=True)),
            [head.pk],
        )

    def test_waiting_members_carry_last_sign_in_and_cant_follows(self):
        pod = self.pod(members=[self.anna, self.mike, self.raj])
        head = self.proposal(pod, age=3 * DAY)
        type(self.raj).objects.filter(pk=self.raj.pk).update(last_login=NOW - 3 * HOUR)
        send_stale_request_alert(now=NOW)
        line = _section_texts(_payload(self.post))[1]
        self.assertTrue(line.endswith(
            ' - waiting on Mike K. (mike@example.com, last sign-in 12 days ago); '
            'Raj P. (raj@example.com, last sign-in today)',
        ))

        PodMeeting.objects.filter(pk=head.pk).update(stuck_alerted_at=None)
        PodMeetingResponse.objects.create(meeting=head, user=self.mike, response=MEETING_RESPONSE_CANT)
        self.post.reset_mock()
        send_stale_request_alert(now=NOW)
        line = _section_texts(_payload(self.post))[1]
        self.assertTrue(line.endswith(
            ' - waiting on Raj P. (raj@example.com, last sign-in today) - can\'t make it: Mike K.',
        ))

        PodMeeting.objects.filter(pk=head.pk).update(stuck_alerted_at=None)
        PodMeetingResponse.objects.create(meeting=head, user=self.raj, response=MEETING_RESPONSE_CANT)
        self.post.reset_mock()
        send_stale_request_alert(now=NOW)
        line = _section_texts(_payload(self.post))[1]
        # Issue #1938: two of three can't make it, so it can't be confirmed.
        self.assertTrue(line.endswith(
            ", proposed by Anna K. 3 days ago - can't make it: Mike K., Raj P."
            " - can't be confirmed, needs another time",
        ))
        self.assertNotIn('waiting on', line)

    def test_moved_proposal_reads_moved_by_with_the_age_of_the_move(self):
        head = self.proposal(self.pod(), age=6 * DAY)
        with freeze_time(NOW - 3 * DAY):
            mtg.move_meeting(head, self.mike, NOW + 2 * DAY, now=NOW - 3 * DAY)
        send_stale_request_alert(now=NOW)
        line = _section_texts(_payload(self.post))[1]
        self.assertIn(' - Sun Oct 11, 08:00 UTC, moved by Mike K. 3 days ago - waiting on Anna K.', line)

    def test_announced_once_then_counted_as_earlier_and_quiet_days_post_nothing(self):
        self.proposal(self.pod('First'), age=4 * DAY)
        send_stale_request_alert(now=NOW)
        self.post.reset_mock()

        self.assertEqual(send_stale_request_alert(now=NOW + DAY), 0)
        self.post.assert_not_called()

        second = self.proposal(self.pod('Second'), age=3 * DAY)
        third = self.proposal(self.pod('Third'), age=50 * HOUR)
        self.assertEqual(send_stale_request_alert(now=NOW), 2)
        payload = _payload(self.post)
        headline, lines = _section_texts(payload)
        self.assertEqual(headline, '*2 pod proposals have waited more than 48 hours or half the time to the meeting*')
        self.assertEqual([line.split('|')[1].split('>')[0] for line in lines.split('\n')], ['Second', 'Third'])
        self.assertEqual(_context_texts(payload), ['1 earlier proposal is still waiting.'])
        for meeting in (second, third):
            meeting.refresh_from_db()
            self.assertEqual(meeting.stuck_alerted_at, NOW)

    def test_headline_uses_the_hours_setting_and_caps_at_ten_lines(self):
        self._set('PODS_STUCK_PROPOSAL_HOURS', '24')
        for i in range(12):
            self.proposal(self.pod(f'Pod {i:02d}'), age=(30 + i) * HOUR)
        self.assertEqual(send_stale_request_alert(now=NOW), 12)
        headline, lines = _section_texts(_payload(self.post))
        rows = lines.split('\n')
        self.assertEqual(headline, '*12 pod proposals have waited more than 24 hours or half the time to the meeting*')
        self.assertEqual(len(rows), 11)
        self.assertIn('|Pod 11>', rows[0])
        self.assertEqual(rows[-1], 'and 2 more')
        self.assertEqual(PodMeeting.objects.filter(stuck_alerted_at=NOW).count(), 12)

    def test_long_lines_are_split_into_sections_under_slacks_limit(self):
        members = [self.anna] + [
            make_user(f'a-very-long-member-address-{i:02d}@example-university-domain.edu', first_name=f'M{i}')
            for i in range(11)
        ]
        for i in range(10):
            self.proposal(self.pod(f'Pod {i}', members=members, max_members=12), age=(50 + i) * HOUR)
        send_stale_request_alert(now=NOW)
        sections = _section_texts(_payload(self.post))
        self.assertGreater(len(sections), 2)
        self.assertTrue(all(len(text) <= 3000 for text in sections))
        self.assertEqual(sum(len(text.split('\n')) for text in sections[1:]), 10)

    def test_member_text_is_escaped_and_cannot_ping(self):
        pod = self.pod('<!channel> & <https://evil.test|click>')
        self.proposal(pod, age=3 * DAY)
        type(self.mike).objects.filter(pk=self.mike.pk).update(first_name='<!here>', last_name='@everyone')
        type(self.anna).objects.filter(pk=self.anna.pk).update(first_name='<@U123>')
        send_stale_request_alert(now=NOW)

        payload = _payload(self.post)
        line = _section_texts(payload)[1]
        self.assertIn('|&lt;!channel&gt; &amp; &lt;https://evil.test|click&gt;>', line)
        self.assertIn('proposed by &lt;@U123&gt; K.', line)
        self.assertIn('waiting on &lt;!here&gt; @. (mike@example.com,', line)
        for raw in ('<!channel>', '<!here>', '<@U123>', '<https://evil.test'):
            self.assertNotIn(raw, line)
        self.assertTrue(all(
            b['text']['verbatim'] for b in payload['blocks'] if b['type'] == 'section'
        ))
        self.assertEqual((payload['link_names'], payload['parse']), (False, 'none'))


@tag('core')
@freeze_time(NOW)
class StudioStuckTest(StuckFixture):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.staff)

    def test_pod_list_badges_only_pods_with_a_stuck_proposal(self):
        stuck = self.pod('Stuck pod')
        self.proposal(stuck, age=3 * DAY)
        fresh = self.pod('Fresh pod')
        self.proposal(fresh, age=2 * HOUR)
        response = self.client.get('/studio/pods/')
        html = response.content.decode()
        rows = {r.split('"', 1)[0]: r for r in html.split('data-pod-id="')[1:]}
        self.assertIn('data-testid="studio-pod-stuck"', rows[str(stuck.pk)])
        self.assertNotIn('data-testid="studio-pod-stuck"', rows[str(fresh.pk)])
        self.assertEqual(texts(response, 'studio-pod-stuck'), ['Stuck proposal'])

    def test_pod_list_never_badges_an_archived_pod(self):
        archived = self.pod('Archived pod', status='archived')
        self.proposal(archived, age=3 * DAY)
        response = self.client.get('/studio/pods/?status=archived')
        html = response.content.decode()
        self.assertIn(f'data-pod-id="{archived.pk}"', html)
        self.assertEqual(texts(response, 'studio-pod-stuck'), [])

    def test_pod_detail_of_an_archived_pod_has_no_stuck_badge(self):
        archived = self.pod('Archived pod', status='archived')
        self.proposal(archived, age=3 * DAY)
        response = self.client.get(f'/studio/pods/{archived.pk}/')
        self.assertEqual(texts(response, 'studio-pod-meeting-no-answer'), ['No answer: Mike K.'])
        self.assertEqual(texts(response, 'studio-pod-meeting-stuck'), [])

    def test_pod_detail_shows_no_answer_and_stuck_badge(self):
        pod = self.pod(members=[self.anna, self.mike, self.raj])
        stuck = self.proposal(pod, age=3 * DAY)
        PodMeetingResponse.objects.create(meeting=stuck, user=self.raj, response=MEETING_RESPONSE_CANT)
        response = self.client.get(f'/studio/pods/{pod.pk}/')
        self.assertEqual(texts(response, 'studio-pod-meeting-no-answer'), ['No answer: Mike K.'])
        self.assertEqual(texts(response, 'studio-pod-meeting-stuck'), ['Stuck'])

    def test_fresh_proposal_has_no_answer_line_but_no_stuck_badge(self):
        pod = self.pod(members=[self.anna, self.mike, self.raj])
        self.proposal(pod, age=HOUR)
        make_meeting(pod, NOW + 20 * DAY)  # scheduled: neither detail
        response = self.client.get(f'/studio/pods/{pod.pk}/')
        self.assertEqual(texts(response, 'studio-pod-meeting-no-answer'), ['No answer: Mike K., Raj P.'])
        self.assertEqual(texts(response, 'studio-pod-meeting-stuck'), [])

    def test_both_keys_are_in_studio_settings_with_docs_links(self):
        response = self.client.get('/studio/settings/')
        for key, anchor in (
            ('PODS_STUCK_PROPOSAL_HOURS', 'pods.md#pods_stuck_proposal_hours'),
            ('PODS_STUCK_PROPOSAL_ALERT_ENABLED', 'pods.md#pods_stuck_proposal_alert_enabled'),
        ):
            self.assertContains(response, key)
            self.assertContains(response, anchor)


# --- Staff API ------------------------------------------------------------------------------

@tag('core')
@freeze_time(NOW)
class StuckApiTest(StuckFixture):
    def test_meeting_payload_has_waiting_on_stuck_and_alerted_at(self):
        staff = make_user('api-staff@example.com', staff=True)
        token = Token.objects.create(user=staff, name='pods')
        pod = self.pod(members=[self.anna, self.mike, self.raj])
        head, tail = self.proposal(pod, age=3 * DAY, series=2)
        PodMeetingResponse.objects.create(meeting=head, user=self.raj, response=MEETING_RESPONSE_CANT)
        PodMeeting.objects.filter(pk=head.pk).update(stuck_alerted_at=NOW - DAY)
        scheduled = make_meeting(pod, NOW + 30 * DAY)

        response = self.client.get(f'/api/pods/{pod.pk}/meetings', HTTP_AUTHORIZATION=f'Token {token.key}')

        meetings = {m['id']: m for m in json.loads(response.content)['meetings']}
        pick = ('waiting_on', 'stuck', 'stuck_alerted_at')
        self.assertEqual(
            {k: meetings[head.pk][k] for k in pick},
            {'waiting_on': ['mike@example.com'], 'stuck': True, 'stuck_alerted_at': '2026-10-08T08:00:00+00:00'},
        )
        for other in (tail, scheduled):
            self.assertEqual(
                {k: meetings[other.pk][k] for k in pick},
                {'waiting_on': [], 'stuck': False, 'stuck_alerted_at': None},
            )

        archived = self.pod('Archived pod', status='archived')
        archived_head = self.proposal(archived, age=3 * DAY)
        response = self.client.get(f'/api/pods/{archived.pk}/meetings', HTTP_AUTHORIZATION=f'Token {token.key}')
        [payload] = json.loads(response.content)['meetings']
        self.assertEqual(
            (payload['id'], payload['waiting_on'], payload['stuck']),
            (archived_head.pk, ['mike@example.com'], False),
        )


# --- PostgreSQL ---------------------------------------------------------------------------

@tag('core', 'postgresql')
@freeze_time(NOW)
class StuckProposalPostgresTest(StuckFixture):
    """The new column, the stuck queries, the conditional mark and the move
    that clears it run unchanged on PostgreSQL (CI ``--tag=postgresql``)."""

    def test_alert_marks_then_move_clears_and_api_reads_it(self):
        staff = make_user('pg-staff@example.com', staff=True)
        token = Token.objects.create(user=staff, name='pods')
        pod = self.pod()
        head, _tail = self.proposal(pod, age=3 * DAY, series=2)

        self.assertEqual(send_stale_request_alert(now=NOW), 1)
        head.refresh_from_db()
        self.assertEqual(head.stuck_alerted_at, NOW)

        mtg.move_meeting(head, self.anna, NOW + 5 * DAY, now=NOW)
        head.refresh_from_db()
        self.assertIsNone(head.stuck_alerted_at)

        response = self.client.get(f'/api/pods/{pod.pk}/meetings', HTTP_AUTHORIZATION=f'Token {token.key}')
        payload = {m['id']: m for m in json.loads(response.content)['meetings']}[head.pk]
        self.assertEqual(
            (payload['waiting_on'], payload['stuck'], payload['stuck_alerted_at']),
            (['mike@example.com'], False, None),
        )
