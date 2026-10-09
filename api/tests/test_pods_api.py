"""Staff pods API (issue #1918)."""

import json

from django.test import TestCase, tag
from freezegun import freeze_time

from accounts.models import Token
from crm.models import CRMRecord
from payments.models import Tier, TierOverride
from pods.models import POD_SOURCE_STUDIO, Pod, PodJoinRequest, PodMembership
from pods.services import membership as svc
from pods.tests.fixtures import enroll, make_cohort, make_course, make_user, set_windows


@tag('core')
class PodsApiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = make_course(slug='ai-buildcamp', title='AI Buildcamp')
        cls.cohort = make_cohort(cls.course, key='4', name='Cohort 4')
        cls.other = make_cohort(cls.course, key='5', name='Cohort 5')
        cls.self_paced = make_cohort(cls.course, key='sp', name='Self-paced', mode='self_paced')
        cls.staff = make_user('staff@test.com', staff=True)
        cls.token = Token.objects.create(user=cls.staff, name='pods')
        # Tokens can only be minted for staff; demote afterwards to get a
        # valid token whose owner is no longer staff.
        cls.member = make_user('member@test.com', staff=True)
        cls.member_token = Token.objects.create(user=cls.member, name='member')
        type(cls.member).objects.filter(pk=cls.member.pk).update(is_staff=False)
        cls.anna = make_user('anna@test.com', first_name='Anna', last_name='Klein', timezone_name='Europe/Berlin')
        cls.raj = make_user('raj@test.com', first_name='Raj')
        cls.mike = make_user('mike@test.com', first_name='Mike')
        cls.outsider = make_user('main@test.com')
        for user in (cls.anna, cls.raj, cls.mike):
            enroll(user, cls.cohort)
        enroll(cls.outsider, cls.other)

    def call(self, method, url, data=None, token=None):
        headers = {'HTTP_AUTHORIZATION': f'Token {(token or self.token).key}'}
        kwargs = {'content_type': 'application/json'} if data is not None else {}
        body = json.dumps(data) if data is not None else None
        return getattr(self.client, method)(url, body, **kwargs, **headers) if body is not None else getattr(self.client, method)(url, **headers)

    def _pod(self, members=(), owner=None, max_members=4, name='Evening builders'):
        pod = Pod.objects.create(
            cohort=self.cohort, name=name, purpose='Ship', owner=owner,
            max_members=max_members, source=POD_SOURCE_STUDIO,
        )
        for user in members:
            PodMembership.objects.create(pod=pod, user=user, source='staff')
        return pod

    def test_every_endpoint_requires_a_staff_token(self):
        pod = self._pod(members=[self.anna])
        urls = [
            ('get', '/api/courses/ai-buildcamp/cohorts/4/members'),
            ('get', '/api/pods'),
            ('post', '/api/pods'),
            ('get', f'/api/pods/{pod.pk}'),
            ('patch', f'/api/pods/{pod.pk}'),
            ('post', f'/api/pods/{pod.pk}/members'),
            ('delete', f'/api/pods/{pod.pk}/members/anna@test.com'),
            ('get', f'/api/pods/{pod.pk}/requests'),
            ('post', f'/api/pods/{pod.pk}/requests/1/approve'),
            ('post', f'/api/pods/{pod.pk}/requests/1/decline'),
        ]
        for method, url in urls:
            self.assertEqual(getattr(self.client, method)(url).status_code, 401, url)
            response = self.call(method, url, token=self.member_token)
            self.assertEqual(response.status_code, 401, url)
            self.assertEqual(response.json()['code'], 'invalid_token', url)
        self.assertTrue(pod.memberships.filter(user=self.anna).exists())
        self.assertEqual(Pod.objects.count(), 1)

    def test_roster_lists_only_enrolled_users_with_crm_availability_and_pods(self):
        main = Tier.objects.get(slug='main')
        TierOverride.objects.create(
            user=self.anna, override_tier=main, original_tier=None, is_active=True,
            expires_at='2099-01-01T00:00:00Z',  # date-rot-ok: far-future override expiry
        )
        CRMRecord.objects.create(user=self.anna, status='active', persona='Builder', summary='Ships', next_steps='Pair')
        set_windows(self.anna, 'Europe/Berlin', [(1, '18:00', '21:00', 'if_needed')])
        pod = self._pod(members=[self.anna])
        requested = self._pod(members=[self.raj], name='Other')
        svc.request_to_join(requested, self.anna)
        response = self.call('get', '/api/courses/ai-buildcamp/cohorts/4/members')
        body = response.json()
        self.assertEqual(body['total'], 3)
        self.assertEqual([m['email'] for m in body['members']], ['anna@test.com', 'raj@test.com', 'mike@test.com'])
        anna = body['members'][0]
        self.assertEqual(anna['tier'], 'main')
        self.assertEqual(anna['crm'], {'status': 'active', 'persona': 'Builder', 'summary': 'Ships', 'next_steps': 'Pair'})
        self.assertEqual(anna['availability']['windows'], [{'weekday': 1, 'start': '18:00', 'end': '21:00', 'preference': 'if_needed'}])
        self.assertEqual((anna['pod_ids'], anna['open_request_pod_ids']), ([pod.pk], [requested.pk]))
        self.assertEqual((body['members'][2]['crm'], body['members'][2]['availability']), (None, None))
        paged = self.call('get', '/api/courses/ai-buildcamp/cohorts/4/members?limit=1&offset=1').json()
        self.assertEqual([m['email'] for m in paged['members']], ['raj@test.com'])
        self.assertEqual(self.call('get', '/api/courses/nope/cohorts/4/members').json()['code'], 'unknown_course')
        self.assertEqual(self.call('get', '/api/courses/ai-buildcamp/cohorts/9/members').json()['code'], 'unknown_cohort')
        self.assertEqual(self.call('get', '/api/courses/ai-buildcamp/cohorts/4/members?limit=501').status_code, 422)

    def test_create_pod_returns_results_and_is_idempotent_for_members(self):
        payload = {
            'course_slug': 'ai-buildcamp', 'cohort_key': '4', 'name': 'Agents pod',
            'purpose': 'Ship an agent demo', 'max_members': 3, 'owner_email': 'anna@test.com',
            'member_emails': ['anna@test.com', 'raj@test.com', 'main@test.com', 'ghost@test.com'],
        }
        response = self.call('post', '/api/pods', payload)
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body['results'], {
            'added': ['anna@test.com', 'raj@test.com'], 'not_enrolled': ['main@test.com'],
            'unknown_user': ['ghost@test.com'], 'already_member': [], 'over_capacity': [],
        })
        self.assertEqual((body['owner_email'], body['source'], body['member_count']), ('anna@test.com', 'api', 2))
        again = self.call('post', f'/api/pods/{body["id"]}/members', {'emails': ['raj@test.com', 'mike@test.com', 'anna@test.com']})
        self.assertEqual(again.json()['results'], {
            'added': ['mike@test.com'], 'not_enrolled': [], 'unknown_user': [],
            'already_member': ['raj@test.com', 'anna@test.com'], 'over_capacity': [],
        })
        self.assertEqual(Pod.objects.get(pk=body['id']).memberships.count(), 3)

    def test_posting_the_same_create_twice_returns_the_same_pod(self):
        payload = {
            'course_slug': 'ai-buildcamp', 'cohort_key': '4', 'name': 'Agents pod',
            'purpose': 'Ship an agent demo', 'owner_email': 'anna@test.com',
            'member_emails': ['anna@test.com', 'raj@test.com'],
        }
        first = self.call('post', '/api/pods', payload)
        second = self.call('post', '/api/pods', {**payload, 'name': 'agents POD'})
        self.assertEqual(first.status_code, 201)
        # An existing pod is returned as OK (not Created).
        self.assertEqual(second.reason_phrase, 'OK')
        self.assertEqual(second.json()['id'], first.json()['id'])
        self.assertEqual(second.json()['results'], {
            'added': [], 'not_enrolled': [], 'unknown_user': [],
            'already_member': ['anna@test.com', 'raj@test.com'], 'over_capacity': [],
        })
        self.assertEqual(Pod.objects.filter(cohort=self.cohort).count(), 1)
        self.assertEqual(PodMembership.objects.filter(user=self.raj).count(), 1)
        third = self.call('post', '/api/pods', {**payload, 'member_emails': ['mike@test.com']})
        self.assertEqual((third.reason_phrase, third.json()['results']['added']), ('OK', ['mike@test.com']))
        memberships = PodMembership.objects.filter(pod_id=first.json()['id'])
        self.assertEqual(sorted(memberships.values_list('source', flat=True)), ['api', 'api', 'api'])
        for membership in memberships:
            membership.full_clean()

    def test_create_rejects_self_paced_and_unknown_cohort(self):
        response = self.call('post', '/api/pods', {
            'course_slug': 'ai-buildcamp', 'cohort_key': 'sp', 'name': 'X', 'purpose': 'Y',
        })
        self.assertEqual((response.status_code, response.json()['code']), (422, 'validation_error'))
        response = self.call('post', '/api/pods', {
            'course_slug': 'ai-buildcamp', 'cohort_key': '99', 'name': 'X', 'purpose': 'Y',
        })
        self.assertEqual((response.status_code, response.json()['code']), (404, 'unknown_cohort'))
        self.assertFalse(Pod.objects.exists())

    def test_list_filters(self):
        self._pod(members=[self.anna])
        closed = self._pod(name='Closed')
        Pod.objects.filter(pk=closed.pk).update(status='closed')
        body = self.call('get', '/api/pods?course=ai-buildcamp&cohort=4&status=closed').json()
        self.assertEqual([p['name'] for p in body['pods']], ['Closed'])
        self.assertEqual(self.call('get', '/api/pods?status=weird').status_code, 422)

    @freeze_time('2026-07-05 00:00:00')  # date-rot-ok: frozen Sunday so slots are deterministic
    def test_detail_includes_members_requests_and_suggested_slots(self):
        set_windows(self.anna, 'Europe/Berlin', [(1, '18:00', '20:00')])
        set_windows(self.raj, 'Europe/Berlin', [(1, '18:00', '20:00')])
        pod = self._pod(members=[self.anna, self.raj], max_members=2)
        svc.request_to_join(pod, self.mike, 'hi')
        body = self.call('get', f'/api/pods/{pod.pk}').json()
        self.assertEqual([m['email'] for m in body['members']], ['anna@test.com', 'raj@test.com'])
        self.assertEqual(body['open_requests'][0]['status'], 'waitlisted')
        self.assertEqual(body['open_requests'][0]['waitlist_position'], 1)
        slot = body['suggested_slots'][0]
        self.assertEqual(slot['start'], '2026-07-07T16:00:00+00:00')
        self.assertEqual(slot['available'], ['anna@test.com', 'raj@test.com'])
        self.assertEqual(slot['local_starts'][0]['local_start'], '2026-07-07T18:00:00+02:00')
        self.assertEqual(self.call('get', '/api/pods/9999').json()['code'], 'unknown_pod')

    def test_patch_rules(self):
        pod = self._pod(members=[self.anna, self.raj], max_members=2)
        waiting = svc.request_to_join(pod, self.mike)
        response = self.call('patch', f'/api/pods/{pod.pk}', {'max_members': 1})
        self.assertEqual(response.status_code, 422)
        response = self.call('patch', f'/api/pods/{pod.pk}', {'max_members': 3, 'owner_email': 'raj@test.com'})
        self.assertEqual(response.json()['owner_email'], 'raj@test.com')
        waiting.refresh_from_db()
        self.assertEqual(waiting.status, 'pending')
        self.assertEqual(self.call('patch', f'/api/pods/{pod.pk}', {'owner_email': 'mike@test.com'}).status_code, 422)
        self.assertEqual(self.call('patch', f'/api/pods/{pod.pk}', {'slack_channel_url': 'http://x.slack.com/a'}).status_code, 422)
        response = self.call('patch', f'/api/pods/{pod.pk}', {'status': 'archived', 'owner_email': None})
        self.assertEqual((response.json()['status'], response.json()['owner_email']), ('archived', None))
        waiting.refresh_from_db()
        self.assertEqual(waiting.status, 'cancelled')
        self.assertEqual(self.call('patch', f'/api/pods/{pod.pk}', {'bogus': 1}).status_code, 422)

    def test_remove_member_and_request_overrides(self):
        pod = self._pod(members=[self.anna], owner=self.anna, max_members=1)
        waiting = svc.request_to_join(pod, self.raj)
        response = self.call('post', f'/api/pods/{pod.pk}/requests/{waiting.pk}/approve', {})
        self.assertEqual((response.status_code, response.json()['code']), (409, 'pod_full'))
        self.assertEqual(self.call('delete', f'/api/pods/{pod.pk}/members/mike@test.com').json()['code'], 'not_a_member')
        response = self.call('delete', f'/api/pods/{pod.pk}/members/ANNA@test.com')
        self.assertEqual(response.status_code, 204)
        pod.refresh_from_db()
        self.assertIsNone(pod.owner)
        response = self.call('post', f'/api/pods/{pod.pk}/requests/{waiting.pk}/approve', {})
        self.assertEqual(response.json()['status'], 'approved')
        response = self.call('post', f'/api/pods/{pod.pk}/requests/{waiting.pk}/decline', {})
        self.assertEqual((response.status_code, response.json()['code']), (409, 'request_not_open'))
        listed = self.call('get', f'/api/pods/{pod.pk}/requests?status=approved').json()['requests']
        self.assertEqual([(r['email'], r['decided_by']) for r in listed], [('raj@test.com', 'staff@test.com')])
        self.assertTrue(PodJoinRequest.objects.filter(pk=waiting.pk, status='approved').exists())

    def test_requests_expose_stale_alerted_at(self):
        """Issue #1927: staff see when the daily Slack alert reported a request."""
        pod = self._pod(members=[self.raj], owner=self.raj)
        announced = svc.request_to_join(pod, self.anna)
        svc.request_to_join(pod, self.mike)
        PodJoinRequest.objects.filter(pk=announced.pk).update(stale_alerted_at='2026-10-08T09:00:00Z')
        listed = self.call('get', f'/api/pods/{pod.pk}/requests').json()['requests']
        self.assertEqual(
            {r['email']: r['stale_alerted_at'] for r in listed},
            {'anna@test.com': '2026-10-08T09:00:00+00:00', 'mike@test.com': None},
        )

    def test_staff_can_add_a_twice_declined_student(self):
        """Issue #1927: the decline rule only limits member requests."""
        pod = self._pod(members=[self.raj], owner=self.raj)
        for _ in range(2):
            request = PodJoinRequest.objects.create(pod=pod, user=self.anna, status='pending')
            svc.decline_request(request, self.raj)
        with self.assertRaises(svc.PodError):
            svc.request_to_join(pod, self.anna)
        response = self.call('post', f'/api/pods/{pod.pk}/members', {'emails': ['anna@test.com']})
        self.assertEqual(response.json()['results']['added'], ['anna@test.com'])
        self.assertTrue(PodMembership.objects.filter(pod=pod, user=self.anna).exists())
