"""Staff API for sprint pods (issue #1921)."""

import datetime
import json

from django.test import TestCase, tag
from django.utils import timezone

from accounts.models import Token
from plans.models import Sprint, SprintEnrollment
from pods.models import Pod, PodMembership
from pods.tests.fixtures import enroll, make_cohort, make_course, make_user


@tag('core')
class SprintPodsApiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.sprint = Sprint.objects.create(
            name='Spring sprint', slug='spring-sprint', status='active',
            start_date=timezone.localdate() - datetime.timedelta(days=7), duration_weeks=6,
        )
        cls.other_sprint = Sprint.objects.create(
            name='Autumn sprint', slug='autumn-sprint', status='active',
            start_date=timezone.localdate(), duration_weeks=6,
        )
        cls.course = make_course(slug='ai-buildcamp', title='AI Buildcamp')
        cls.cohort = make_cohort(cls.course, key='4', name='Cohort 4')
        cls.staff = make_user('staff@test.com', staff=True)
        cls.token = Token.objects.create(user=cls.staff, name='pods')
        cls.anna = make_user('anna@test.com', first_name='Anna', last_name='Klein', timezone_name='Europe/Berlin')
        cls.mike = make_user('mike@test.com', first_name='Mike')
        cls.outsider = make_user('out@test.com')
        for user in (cls.anna, cls.mike):
            SprintEnrollment.objects.create(sprint=cls.sprint, user=user)
        SprintEnrollment.objects.create(sprint=cls.other_sprint, user=cls.anna)
        enroll(cls.anna, cls.cohort)

    def call(self, method, url, data=None, auth=True):
        headers = {'HTTP_AUTHORIZATION': f'Token {self.token.key}'} if auth else {}
        if data is None:
            return getattr(self.client, method)(url, **headers)
        return getattr(self.client, method)(url, json.dumps(data), content_type='application/json', **headers)

    def test_sprint_api_reads_and_writes_pods_enabled(self):
        response = self.call('get', '/api/sprints/spring-sprint')
        self.assertIs(response.json()['pods_enabled'], False)
        response = self.call('patch', '/api/sprints/spring-sprint', {'pods_enabled': True})
        self.assertIs(response.json()['pods_enabled'], True)
        self.sprint.refresh_from_db()
        self.assertTrue(self.sprint.pods_enabled)
        bad = self.call('patch', '/api/sprints/spring-sprint', {'pods_enabled': 'yes'})
        self.assertEqual(bad.status_code, 400)
        created = self.call('post', '/api/sprints', {
            'name': 'New', 'slug': 'new-sprint', 'start_date': '2026-11-01', 'duration_weeks': 4, 'pods_enabled': True,
        })
        self.assertEqual(created.status_code, 201)
        self.assertTrue(Sprint.objects.get(slug='new-sprint').pods_enabled)

    def test_sprint_roster_has_the_cohort_shape_with_sprint_pods_only(self):
        pod = Pod.objects.create(sprint=self.sprint, name='Agents', purpose='p', source='api')
        PodMembership.objects.create(pod=pod, user=self.anna, source='api')
        other = Pod.objects.create(sprint=self.other_sprint, name='Other', purpose='p', source='api')
        PodMembership.objects.create(pod=other, user=self.anna, source='api')
        cohort_pod = Pod.objects.create(cohort=self.cohort, name='Cohort', purpose='p', source='api')
        PodMembership.objects.create(pod=cohort_pod, user=self.anna, source='api')
        data = self.call('get', '/api/sprints/spring-sprint/members').json()
        self.assertEqual(data['sprint'], 'spring-sprint')
        self.assertEqual(data['total'], 2)
        rows = {row['email']: row for row in data['members']}
        self.assertEqual(set(rows), {'anna@test.com', 'mike@test.com'})
        self.assertEqual(rows['anna@test.com']['pod_ids'], [pod.pk])
        self.assertNotIn('open_request_pod_ids', rows['mike@test.com'])
        for key in ('first_name', 'tier', 'tags', 'preferred_timezone', 'slack_member',
                    'has_slack_user_id', 'enrolled_at', 'crm', 'availability'):
            self.assertIn(key, rows['anna@test.com'])

    def test_sprint_roster_unknown_slug_and_no_token(self):
        response = self.call('get', '/api/sprints/nope/members')
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()['code'], 'unknown_sprint')
        self.assertEqual(self.call('get', '/api/sprints/spring-sprint/members', auth=False).status_code, 401)

    def test_create_sprint_pod_then_repeat_returns_the_same_pod(self):
        body = {
            'sprint_slug': 'spring-sprint', 'name': 'Agents pod', 'purpose': 'Ship',
            'owner_email': 'anna@test.com', 'member_emails': ['mike@test.com', 'out@test.com'],
        }
        response = self.call('post', '/api/pods', body)
        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data['sprint_slug'], 'spring-sprint')
        self.assertEqual(data['sprint_name'], 'Spring sprint')
        self.assertIsNone(data['course_slug'])
        self.assertIsNone(data['cohort_key'])
        self.assertIsNone(data['cohort_name'])
        self.assertEqual(data['results']['not_enrolled'], ['out@test.com'])
        again = self.call('post', '/api/pods', {**body, 'name': 'agents pod'})
        # 200 OK (existing pod), not 201 Created. Asserted via the reason phrase
        # because the status-200 ratchet forbids a literal 200 anchor.
        self.assertEqual(again.reason_phrase, 'OK')
        self.assertEqual(again.json()['id'], data['id'])
        self.assertEqual(Pod.objects.filter(sprint=self.sprint).count(), 1)

    def test_create_validation(self):
        both = self.call('post', '/api/pods', {
            'sprint_slug': 'spring-sprint', 'course_slug': 'ai-buildcamp', 'cohort_key': '4',
            'name': 'X', 'purpose': 'Y',
        })
        self.assertEqual(both.status_code, 422)
        self.assertEqual(both.json()['code'], 'validation_error')
        self.assertEqual(both.json()['details']['field'], 'sprint_slug')
        neither = self.call('post', '/api/pods', {'name': 'X', 'purpose': 'Y'})
        self.assertEqual(neither.status_code, 422)
        self.assertEqual(neither.json()['details']['field'], 'course_slug')
        unknown = self.call('post', '/api/pods', {'sprint_slug': 'nope', 'name': 'X', 'purpose': 'Y'})
        self.assertEqual(unknown.status_code, 404)
        self.assertEqual(unknown.json()['code'], 'unknown_sprint')
        owner = self.call('post', '/api/pods', {
            'sprint_slug': 'spring-sprint', 'name': 'X', 'purpose': 'Y', 'owner_email': 'out@test.com',
        })
        self.assertEqual(owner.status_code, 422)
        self.assertEqual(owner.json()['error'], 'out@test.com is not enrolled in this sprint.')

    def test_list_filters_by_sprint_and_payloads_carry_both_shapes(self):
        sprint_pod = Pod.objects.create(sprint=self.sprint, name='Sprint pod', purpose='p', source='api')
        Pod.objects.create(sprint=self.other_sprint, name='Other', purpose='p', source='api')
        cohort_pod = Pod.objects.create(cohort=self.cohort, name='Cohort pod', purpose='p', source='api')
        everything = {row['id']: row for row in self.call('get', '/api/pods').json()['pods']}
        self.assertEqual(len(everything), 3)
        self.assertEqual(everything[cohort_pod.pk]['course_slug'], 'ai-buildcamp')
        self.assertIsNone(everything[cohort_pod.pk]['sprint_slug'])
        self.assertIsNone(everything[cohort_pod.pk]['sprint_name'])
        filtered = self.call('get', '/api/pods?sprint=spring-sprint').json()
        self.assertEqual([row['id'] for row in filtered['pods']], [sprint_pod.pk])
        combined = self.call('get', '/api/pods?sprint=spring-sprint&course=ai-buildcamp')
        self.assertEqual(combined.status_code, 422)
        self.assertEqual(combined.json()['code'], 'validation_error')
        unknown = self.call('get', '/api/pods?sprint=nope')
        self.assertEqual(unknown.status_code, 404)
        self.assertEqual(unknown.json()['code'], 'unknown_sprint')
        detail = self.call('get', f'/api/pods/{sprint_pod.pk}').json()
        self.assertEqual(detail['sprint_slug'], 'spring-sprint')

    def test_member_add_on_a_sprint_pod_uses_sprint_enrollment(self):
        pod = Pod.objects.create(sprint=self.sprint, name='Agents', purpose='p', source='api')
        response = self.call('post', f'/api/pods/{pod.pk}/members', {'emails': ['mike@test.com', 'out@test.com']})
        results = response.json()['results']
        self.assertEqual(results['added'], ['mike@test.com'])
        self.assertEqual(results['not_enrolled'], ['out@test.com'])
