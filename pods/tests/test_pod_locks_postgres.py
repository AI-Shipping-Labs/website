"""PostgreSQL regression coverage for the pod row locks (issue #1919).

``meetings._lock_pod`` and ``membership._lock`` locked the pod with a bare
``select_for_update()`` on a queryset that also ``select_related`` the
nullable ``Pod.cohort`` (and ``Pod.owner``). That compiles to a LEFT OUTER
JOIN and PostgreSQL rejects it with

    NotSupportedError: FOR UPDATE cannot be applied to the nullable side of
    an outer join

so every meeting write and every join request / approve / decline / leave /
pod edit returned a 500 in production. SQLite drops the locking clause, so
the SQLite suite never saw it. These tests only mean something on
PostgreSQL and run in the CI ``PostgreSQL 16 Verification`` job
(``manage.py test --tag=postgresql``).
"""

import datetime
import json

from django.db import connection
from django.test import TestCase, tag
from django.utils import timezone

from accounts.models import Token
from pods.models import MEETING_RESPONSE_CANT, MEETING_RESPONSE_GOING, PodJoinRequest, PodMeeting
from pods.services import meetings as mtg
from pods.services import membership as svc
from pods.tests.fixtures import enroll, make_cohort, make_course, make_meeting, make_pod, make_user


def _quarter(days, hour=16):
    """``days`` from now at ``hour``:00 UTC (always on the quarter hour)."""
    day = (timezone.now() + datetime.timedelta(days=days)).date()
    return datetime.datetime(day.year, day.month, day.day, hour, 0, tzinfo=datetime.UTC)


@tag('core', 'postgresql')
class PodLockPostgresTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = make_course()
        cls.cohort = make_cohort(cls.course)
        cls.anna = make_user('anna@example.com', first_name='Anna', last_name='Klein', timezone_name='Europe/Berlin')
        cls.raj = make_user('raj@example.com', first_name='Raj', last_name='Shah', timezone_name='Asia/Kolkata')
        cls.mike = make_user('mike@example.com', first_name='Mike', last_name='Kay', timezone_name='America/New_York')
        cls.staff = make_user('staff@example.com', staff=True)
        for user in (cls.anna, cls.raj, cls.mike):
            enroll(user, cls.cohort)

    def setUp(self):
        if connection.vendor != 'postgresql':
            self.skipTest(
                'FOR UPDATE over a nullable outer join is a PostgreSQL-only '
                'failure; SQLite drops the locking clause entirely'
            )

    def pod(self, members, **kwargs):
        return make_pod(self.cohort, 'RAG evals study group', members, owner=members[0], **kwargs)

    def test_member_meeting_lifecycle_runs_under_the_pod_lock(self):
        pod = self.pod([self.anna, self.raj, self.mike])
        meetings = mtg.propose_meeting(pod, self.anna, _quarter(2), repeat_weekly=True)
        self.assertEqual(len(meetings), 4)
        self.assertTrue(mtg.respond_to_meeting(meetings[0], self.raj, MEETING_RESPONSE_GOING))
        self.assertEqual(set(PodMeeting.objects.filter(pod=pod).values_list('status', flat=True)), {'scheduled'})
        mtg.respond_to_meeting(meetings[1], self.mike, MEETING_RESPONSE_CANT)
        [moved] = mtg.move_meeting(meetings[1], self.anna, _quarter(10, 17))
        self.assertEqual(moved.starts_at, _quarter(10, 17))
        mtg.cancel_meeting(meetings[3], self.raj)
        started = make_meeting(pod, timezone.now() - datetime.timedelta(hours=2))
        mtg.record_outcome(started, self.mike, mtg.OUTCOME_HELD)
        self.assertEqual(
            sorted(PodMeeting.objects.filter(pod=pod).values_list('status', flat=True)),
            ['cancelled', 'held', 'scheduled', 'scheduled', 'scheduled'],
        )
        mtg.set_call_link(pod, self.anna, 'https://meet.google.com/abc-defg-hij')

    def test_staff_schedule_and_status_run_under_the_pod_lock(self):
        pod = self.pod([self.anna, self.raj])
        created = mtg.schedule_meetings(
            pod, self.staff, _quarter(3), zone_name='Europe/Berlin', repeat_weekly=True, created_via='studio',
        )
        mtg.set_meeting_status(created[0], self.staff, 'cancelled')
        mtg.set_meeting_status(created[0], self.staff, 'scheduled')
        self.assertEqual(PodMeeting.objects.filter(pod=pod, status='scheduled').count(), 4)

    def test_join_request_approve_decline_leave_and_edit_run_under_the_pod_lock(self):
        pod = self.pod([self.anna], source='member', max_members=3)
        request = svc.request_to_join(pod, self.raj, 'Building an eval harness')
        svc.approve_request(request, self.anna)
        declined = svc.decline_request(svc.request_to_join(pod, self.mike), self.anna)
        self.assertEqual(declined.status, 'declined')
        make_meeting(pod, timezone.now() + datetime.timedelta(days=2))
        make_meeting(pod, timezone.now() + datetime.timedelta(days=3))
        with self.assertRaisesMessage(svc.PodError, 'This pod already has 2 meetings planned or held.'):
            svc.update_pod(pod, {'meeting_count': '1'}, actor=self.anna)
        svc.update_pod(pod, {'meeting_count': '3', 'meeting_url': 'https://zoom.us/j/1'}, actor=self.anna)
        svc.add_members_by_email(pod, ['mike@example.com'], actor=self.staff, source='staff')
        svc.remove_member(pod, self.raj, actor=self.raj)
        self.assertEqual(sorted(pod.memberships.values_list('user__email', flat=True)),
                         ['anna@example.com', 'mike@example.com'])
        self.assertTrue(PodJoinRequest.objects.filter(pod=pod, user=self.raj, status='approved').exists())

    def test_member_and_api_endpoints_write_meetings_on_postgres(self):
        pod = self.pod([self.anna, self.raj])
        with self.settings(PODS_COURSE_SLUGS=self.course.slug):
            self.client.force_login(self.anna)
            response = self.client.post(
                f'/courses/{self.course.slug}/home/pods/{pod.pk}/meetings/new',
                {'start': _quarter(2).isoformat()},
            )
            self.assertRedirects(response, svc.pod_url(pod), fetch_redirect_response=False)
        meeting = PodMeeting.objects.get(pod=pod)
        token = Token.objects.create(user=self.staff, name='pods')
        response = self.client.patch(
            f'/api/pods/{pod.pk}/meetings/{meeting.pk}', json.dumps({'status': 'scheduled'}),
            content_type='application/json', HTTP_AUTHORIZATION=f'Token {token.key}',
        )
        self.assertEqual(response.json()['meetings'][0]['status'], 'scheduled')
