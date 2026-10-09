"""Staff API for pod meetings (issue #1919).

Every test freezes the clock (date-rot-ok: fixed Friday 2026-10-09).
"""

import datetime
import json
import uuid
from datetime import UTC

from django.test import TestCase, tag
from freezegun import freeze_time

from accounts.models import Token
from notifications.models import Notification
from pods.models import PodMeeting, PodMeetingResponse
from pods.tests.fixtures import enroll, make_cohort, make_course, make_meeting, make_pod, make_user

NOW = datetime.datetime(2026, 10, 9, 8, 0, tzinfo=UTC)  # date-rot-ok: frozen Friday


@tag('core')
@freeze_time(NOW)
class PodMeetingsApiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = make_course(slug='ai-buildcamp', title='AI Buildcamp')
        cls.cohort = make_cohort(cls.course)
        cls.staff = make_user('staff@test.com', staff=True)
        cls.token = Token.objects.create(user=cls.staff, name='pods')
        cls.member = make_user('member@test.com', staff=True)
        cls.member_token = Token.objects.create(user=cls.member, name='member')
        type(cls.member).objects.filter(pk=cls.member.pk).update(is_staff=False)
        cls.anna = make_user('anna@test.com', first_name='Anna', last_name='Klein', timezone_name='Europe/Berlin')
        cls.raj = make_user('raj@test.com', first_name='Raj', last_name='Shah', timezone_name='Asia/Kolkata')
        for user in (cls.anna, cls.raj):
            enroll(user, cls.cohort)

    def setUp(self):
        self.pod = make_pod(self.cohort, 'RAG evals study group', [self.anna, self.raj], owner=self.anna)

    def call(self, method, url, data=None, token=None):
        headers = {'HTTP_AUTHORIZATION': f'Token {(token or self.token).key}'}
        if data is None:
            return getattr(self.client, method)(url, **headers)
        return getattr(self.client, method)(url, json.dumps(data), content_type='application/json', **headers)

    def url(self, suffix=''):
        return f'/api/pods/{self.pod.pk}/meetings{suffix}'

    def test_non_staff_tokens_get_the_standard_auth_error(self):
        meeting = make_meeting(self.pod, NOW + datetime.timedelta(days=3))
        for method, url, body in (
            ('get', self.url(), None),
            ('post', self.url(), {'starts_at': '2026-10-13T18:00:00+02:00', 'timezone': 'UTC'}),
            ('patch', self.url(f'/{meeting.pk}'), {'status': 'cancelled'}),
        ):
            response = self.call(method, url, body, token=self.member_token)
            self.assertEqual(response.status_code, 401, url)
            self.assertEqual(response.json()['code'], 'invalid_token', url)
        self.assertEqual(PodMeeting.objects.count(), 1)
        meeting.refresh_from_db()
        self.assertEqual(meeting.status, 'scheduled')

    def test_post_weekly_creates_scheduled_meetings_and_notifies_members(self):
        response = self.call('post', self.url(), {
            'starts_at': '2026-10-13T18:00:00+02:00', 'timezone': 'Europe/Berlin', 'repeat_weekly': True,
        })
        self.assertEqual(response.status_code, 201)
        meetings = response.json()['meetings']
        self.assertEqual([m['number'] for m in meetings], [1, 2, 3, 4])
        self.assertEqual({m['status'] for m in meetings}, {'scheduled'})
        self.assertEqual({m['created_via'] for m in meetings}, {'api'})
        self.assertEqual(len({m['series_id'] for m in meetings}), 1)
        self.assertEqual(
            [m['starts_at'] for m in meetings],
            ['2026-10-13T16:00:00+00:00', '2026-10-20T16:00:00+00:00',
             '2026-10-27T17:00:00+00:00', '2026-11-03T17:00:00+00:00'],
        )
        self.assertEqual(meetings[0]['ends_at'], '2026-10-13T17:00:00+00:00')
        self.assertEqual(
            list(Notification.objects.filter(user=self.raj).values_list('title', flat=True)),
            ['RAG evals study group meets Tue Oct 13, 21:30 Asia/Kolkata'],
        )

    def test_post_errors(self):
        make_meeting(self.pod, datetime.datetime(2026, 10, 13, 16, 0, tzinfo=UTC))
        cases = [
            ({'starts_at': '2026-10-09T08:30:00+00:00', 'timezone': 'UTC'}, 422, 'validation_error'),
            ({'starts_at': '2027-06-01T08:00:00+00:00', 'timezone': 'UTC'}, 422, 'validation_error'),
            ({'starts_at': '2026-10-13T16:30:00+00:00', 'timezone': 'UTC'}, 422, 'validation_error'),
            ({'starts_at': '2026-10-13T18:10:00+00:00', 'timezone': 'UTC'}, 422, 'validation_error'),
            ({'starts_at': '2026-10-13T18:00:00', 'timezone': 'UTC'}, 422, 'validation_error'),
            ({'starts_at': '2026-10-13T18:00:00+00:00', 'timezone': 'Mars/Base'}, 422, 'validation_error'),
            ({'starts_at': '2026-10-13T18:00:00+00:00'}, 422, 'validation_error'),
            ({'starts_at': '2026-10-15T18:00:00+00:00', 'timezone': 'UTC', 'count': 9, 'repeat_weekly': True},
             409, 'meeting_limit_reached'),
            ({'starts_at': '2026-10-15T18:00:00+00:00', 'timezone': 'UTC', 'extra': 1}, 422, 'validation_error'),
        ]
        for body, status, code in cases:
            with self.subTest(body=body):
                response = self.call('post', self.url(), body)
                self.assertEqual((response.status_code, response.json()['code']), (status, code))
        self.assertEqual(PodMeeting.objects.count(), 1)

    def test_get_lists_meetings_with_responses_and_filters_by_status(self):
        scheduled = make_meeting(self.pod, NOW + datetime.timedelta(days=3), proposed_by=self.anna)
        make_meeting(self.pod, NOW + datetime.timedelta(days=5), status='cancelled')
        expired = make_meeting(self.pod, NOW - datetime.timedelta(hours=1), status='proposed')
        PodMeetingResponse.objects.create(meeting=scheduled, user=self.raj, response='cant_make_it')
        response = self.call('get', self.url())
        rows = {row['id']: row for row in response.json()['meetings']}
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[scheduled.pk]['number'], 1)
        self.assertEqual(rows[scheduled.pk]['responses'], [{'email': 'raj@test.com', 'response': 'cant_make_it'}])
        self.assertEqual(rows[scheduled.pk]['proposed_by'], 'anna@test.com')
        self.assertEqual((rows[expired.pk]['expired'], rows[expired.pk]['number']), (True, None))
        cancelled = self.call('get', self.url('?status=cancelled')).json()['meetings']
        self.assertEqual([row['status'] for row in cancelled], ['cancelled'])
        self.assertEqual(self.call('get', self.url('?status=nope')).status_code, 422)

    def test_pod_detail_includes_call_link_and_non_cancelled_meetings(self):
        self.pod.meeting_url = 'https://meet.google.com/abc-defg-hij'
        self.pod.save()
        live = make_meeting(self.pod, NOW + datetime.timedelta(days=3))
        make_meeting(self.pod, NOW + datetime.timedelta(days=5), status='cancelled')
        data = self.call('get', f'/api/pods/{self.pod.pk}').json()
        self.assertEqual(data['meeting_url'], 'https://meet.google.com/abc-defg-hij')
        self.assertEqual([m['id'] for m in data['meetings']], [live.pk])

    def test_patch_pod_meeting_url_and_meeting_count_guard(self):
        response = self.call('patch', f'/api/pods/{self.pod.pk}', {'meeting_url': 'http://zoom.us/j/1'})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()['details']['field'], 'meeting_url')
        response = self.call('patch', f'/api/pods/{self.pod.pk}', {'meeting_url': 'https://zoom.us/j/1'})
        self.assertEqual(response.json()['meeting_url'], 'https://zoom.us/j/1')
        make_meeting(self.pod, NOW + datetime.timedelta(days=3))
        make_meeting(self.pod, NOW + datetime.timedelta(days=4))
        response = self.call('patch', f'/api/pods/{self.pod.pk}', {'meeting_count': 1})
        self.assertEqual((response.status_code, response.json()['code']), (422, 'validation_error'))
        self.assertEqual(
            response.json()['error'],
            'This pod already has 2 meetings planned or held. Cancel a meeting before lowering the number.',
        )
        response = self.call('patch', f'/api/pods/{self.pod.pk}', {'meeting_url': ''})
        self.assertEqual(response.json()['meeting_url'], '')

    def test_patch_moves_with_move_later_and_marks_held(self):
        series = uuid.uuid4()
        first = make_meeting(self.pod, NOW - datetime.timedelta(hours=2), zone='Europe/Berlin', series_id=series)
        second = make_meeting(self.pod, datetime.datetime(2026, 10, 13, 16, 0, tzinfo=UTC), zone='Europe/Berlin',
                              series_id=series)
        third = make_meeting(self.pod, datetime.datetime(2026, 10, 20, 16, 0, tzinfo=UTC), zone='Europe/Berlin',
                             series_id=series)
        response = self.call('patch', self.url(f'/{second.pk}'), {
            'starts_at': '2026-10-15T17:00:00+02:00', 'move_later': True,
        })
        moved = response.json()['meetings']
        self.assertEqual([m['id'] for m in moved], [second.pk, third.pk])
        self.assertEqual([m['starts_at'] for m in moved], ['2026-10-15T15:00:00+00:00', '2026-10-22T15:00:00+00:00'])
        self.assertEqual(moved[0]['moved_by'], 'staff@test.com')
        self.assertEqual(moved[0]['previous_starts_at'], '2026-10-13T16:00:00+00:00')
        response = self.call('patch', self.url(f'/{first.pk}'), {'status': 'held'})
        self.assertEqual(response.json()['meetings'][0]['status'], 'held')

    def test_patch_errors(self):
        past = make_meeting(self.pod, NOW - datetime.timedelta(hours=2))
        cancelled = make_meeting(self.pod, NOW + datetime.timedelta(days=2), status='cancelled')
        other = make_pod(self.cohort, 'Other', [self.anna])
        foreign = make_meeting(other, NOW + datetime.timedelta(days=2))
        for meeting in (past, cancelled):
            response = self.call('patch', self.url(f'/{meeting.pk}'), {'starts_at': '2026-10-15T17:00:00+00:00'})
            self.assertEqual((response.status_code, response.json()['code']), (409, 'meeting_not_movable'))
        response = self.call('patch', self.url(f'/{foreign.pk}'), {'status': 'held'})
        self.assertEqual((response.status_code, response.json()['code']), (404, 'unknown_meeting'))
        foreign.refresh_from_db()
        self.assertEqual(foreign.status, 'scheduled')
        for body in ({}, {'status': 'held', 'starts_at': '2026-10-15T17:00:00+00:00'}, {'status': 'proposed'},
                     {'starts_at': '2026-10-15T17:05:00+00:00'}, {'starts_at': 'soon'}):
            with self.subTest(body=body):
                response = self.call('patch', self.url(f'/{past.pk}' if 'status' in body else f'/{cancelled.pk}'), body)
                self.assertIn(response.status_code, (409, 422))
        self.pod.meeting_count = 1
        self.pod.save()
        make_meeting(self.pod, NOW + datetime.timedelta(days=5))
        response = self.call('patch', self.url(f'/{cancelled.pk}'), {'status': 'scheduled'})
        self.assertEqual((response.status_code, response.json()['code']), (409, 'meeting_limit_reached'))

    def test_held_on_a_proposal_is_a_validation_error(self):
        proposal = make_meeting(self.pod, NOW + datetime.timedelta(days=2), status='proposed')
        response = self.call('patch', self.url(f'/{proposal.pk}'), {'status': 'held'})
        self.assertEqual((response.status_code, response.json()['code']), (422, 'validation_error'))
        self.assertEqual(response.json()['error'], 'Confirm the proposed time before marking it as held.')
        proposal.refresh_from_db()
        self.assertEqual(proposal.status, 'proposed')
