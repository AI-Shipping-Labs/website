"""Staff study-groups API after unlimited self-service membership (#1984)."""

import json

from django.test import TestCase, tag

from accounts.models import Token
from crm.models import CRMRecord
from payments.models import Tier, TierOverride
from pods.models import POD_SOURCE_STUDIO, Pod, PodJoinRequest, PodMembership
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
        kwargs = {}
        body = None
        if data is not None:
            kwargs = {'content_type': 'application/json'}
            body = json.dumps(data)
        handler = getattr(self.client, method)
        if body is not None:
            return handler(url, body, **kwargs, **headers)
        return handler(url, **headers)

    def group(self, members=(), owner=None, name='Evening builders', **extra):
        pod = Pod.objects.create(
            cohort=self.cohort, name=name, purpose='Ship together.', owner=owner,
            source=POD_SOURCE_STUDIO, **extra,
        )
        for user in members:
            PodMembership.objects.create(pod=pod, user=user, source='staff')
        return pod

    def test_active_endpoints_require_a_staff_token(self):
        pod = self.group(members=[self.anna])
        urls = [
            ('get', '/api/courses/ai-buildcamp/cohorts/4/members'),
            ('get', '/api/pods'), ('post', '/api/pods'),
            ('get', f'/api/pods/{pod.pk}'), ('patch', f'/api/pods/{pod.pk}'),
            ('post', f'/api/pods/{pod.pk}/members'),
            ('delete', f'/api/pods/{pod.pk}/members/anna@test.com'),
            ('get', f'/api/pods/{pod.pk}/requests'),
            ('post', f'/api/pods/{pod.pk}/requests/1/approve'),
            ('post', f'/api/pods/{pod.pk}/requests/1/decline'),
        ]
        for method, url in urls:
            self.assertEqual(getattr(self.client, method)(url).status_code, 401, url)
            response = self.call(method, url, token=self.member_token)
            self.assertEqual((response.status_code, response.json()['code']), (401, 'invalid_token'), url)

    def test_roster_lists_enrolled_users_and_confirmed_memberships_only(self):
        main = Tier.objects.get(slug='main')
        TierOverride.objects.create(
            user=self.anna, override_tier=main, original_tier=None, is_active=True,
            expires_at='2099-01-01T00:00:00Z',  # date-rot-ok
        )
        CRMRecord.objects.create(user=self.anna, status='active', persona='Builder', summary='Ships')
        set_windows(self.anna, 'Europe/Berlin', [(1, '18:00', '21:00', 'if_needed')])
        pod = self.group(members=[self.anna])
        historical = self.group(members=[self.raj], name='Historical')
        PodJoinRequest.objects.create(pod=historical, user=self.anna, status='cancelled')
        body = self.call('get', '/api/courses/ai-buildcamp/cohorts/4/members').json()
        anna = body['members'][0]
        self.assertEqual([row['email'] for row in body['members']], [
            'anna@test.com', 'raj@test.com', 'mike@test.com',
        ])
        self.assertEqual(anna['pod_ids'], [pod.pk])
        self.assertNotIn('open_request_pod_ids', anna)
        self.assertEqual(anna['availability']['timezone'], 'Europe/Berlin')

    def test_create_and_member_add_are_unlimited_idempotent_with_all_copy_fields(self):
        payload = {
            'course_slug': 'ai-buildcamp', 'cohort_key': '4', 'name': 'Agents group',
            'purpose': 'Build and review technical agents.', 'scheduling_note': 'Choose a weekly time after joining.',
            'internal_notes': 'Imported from the operator worksheet.',
            'owner_email': 'anna@test.com',
            'member_emails': ['anna@test.com', 'raj@test.com', 'main@test.com', 'ghost@test.com'],
        }
        response = self.call('post', '/api/pods', payload)
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body['results'], {
            'added': ['anna@test.com', 'raj@test.com'],
            'not_enrolled': ['main@test.com'], 'unknown_user': ['ghost@test.com'],
            'already_member': [],
        })
        self.assertEqual((body['purpose'], body['scheduling_note'], body['internal_notes']), (
            payload['purpose'], payload['scheduling_note'], payload['internal_notes'],
        ))
        again = self.call('post', f'/api/pods/{body["id"]}/members', {
            'emails': ['raj@test.com', 'mike@test.com', 'anna@test.com'],
        })
        self.assertEqual(again.json()['results'], {
            'added': ['mike@test.com'], 'not_enrolled': [], 'unknown_user': [],
            'already_member': ['raj@test.com', 'anna@test.com'],
        })
        self.assertEqual(Pod.objects.get(pk=body['id']).memberships.count(), 3)
        pod_count = Pod.objects.count()
        repeated = self.call('post', '/api/pods', payload)
        repeated_body = repeated.json()
        self.assertEqual(repeated_body['id'], body['id'])
        self.assertEqual(repeated_body['results'], {
            'added': [], 'not_enrolled': ['main@test.com'],
            'unknown_user': ['ghost@test.com'],
            'already_member': ['anna@test.com', 'raj@test.com'],
        })
        self.assertEqual(Pod.objects.count(), pod_count)
        self.assertEqual(Pod.objects.get(pk=body['id']).memberships.count(), 3)
        self.assertTrue({'max_members', 'open_requests'}.isdisjoint(body))

    def test_retired_max_members_write_has_exact_422_on_create_and_patch(self):
        create = self.call('post', '/api/pods', {
            'course_slug': 'ai-buildcamp', 'cohort_key': '4', 'name': 'Agents',
            'purpose': 'Build agents.', 'max_members': 4,
        })
        self.assertEqual(create.status_code, 422)
        self.assertEqual(create.json(), {
            'error': 'Study groups no longer have a size limit.',
            'code': 'validation_error', 'details': {'field': 'max_members'},
        })
        pod = self.group()
        patch = self.call('patch', f'/api/pods/{pod.pk}', {'max_members': 99})
        self.assertEqual((patch.status_code, patch.json()), (422, create.json()))
        pod.refresh_from_db()
        self.assertEqual(pod.max_members, 4)

    def test_patch_updates_public_and_staff_copy(self):
        pod = self.group(internal_notes='old')
        response = self.call('patch', f'/api/pods/{pod.pk}', {
            'purpose': 'Reviewed public purpose.',
            'scheduling_note': 'Neutral scheduling guidance.',
            'internal_notes': 'Preserved formation history.',
        })
        body = response.json()
        self.assertEqual((body['purpose'], body['scheduling_note'], body['internal_notes']), (
            'Reviewed public purpose.', 'Neutral scheduling guidance.', 'Preserved formation history.',
        ))
        pod.refresh_from_db()
        self.assertEqual((pod.purpose, pod.scheduling_note, pod.internal_notes), (
            'Reviewed public purpose.', 'Neutral scheduling guidance.', 'Preserved formation history.',
        ))

    def test_historical_requests_are_read_only_and_mutations_return_stable_gone(self):
        pod = self.group()
        request = PodJoinRequest.objects.create(
            pod=pod, user=self.anna, status='cancelled', message='Historical note',
        )
        listed = self.call('get', f'/api/pods/{pod.pk}/requests').json()['requests']
        self.assertEqual((listed[0]['id'], listed[0]['status']), (request.pk, 'cancelled'))
        for action in ('approve', 'decline'):
            response = self.call('post', f'/api/pods/{pod.pk}/requests/{request.pk}/{action}', {})
            self.assertEqual(response.status_code, 410)
            self.assertEqual(response.json(), {
                'error': 'Study group join requests are no longer available.', 'code': 'gone',
            })
        request.refresh_from_db()
        self.assertEqual(request.status, 'cancelled')
        self.assertFalse(pod.memberships.exists())

    def test_create_rejects_self_paced_and_unknown_scope(self):
        payload = {'course_slug': 'ai-buildcamp', 'cohort_key': 'sp', 'name': 'A', 'purpose': 'B'}
        self.assertEqual(self.call('post', '/api/pods', payload).status_code, 422)
        payload['cohort_key'] = '9'
        self.assertEqual(self.call('post', '/api/pods', payload).status_code, 404)
