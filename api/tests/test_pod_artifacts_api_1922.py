"""Staff pods API: notes and transcripts (issue #1922)."""

import datetime

from django.test import TestCase, tag
from django.utils import timezone

from accounts.models import Token
from api.serializers.datetime import isoformat_or_none
from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting
from pods.models import PodArtifact
from pods.tests.fixtures import enroll, make_cohort, make_course, make_meeting, make_pod, make_user


@tag('core')
class PodArtifactsApiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        # Not in PODS_COURSE_SLUGS: the API works regardless.
        cls.course = make_course(slug='ai-buildcamp', title='AI Buildcamp')
        cls.cohort = make_cohort(cls.course)
        cls.staff = make_user('staff@test.com', staff=True)
        cls.token = Token.objects.create(user=cls.staff, name='pods')
        cls.member = make_user('member@test.com', staff=True)
        cls.member_token = Token.objects.create(user=cls.member, name='member')
        type(cls.member).objects.filter(pk=cls.member.pk).update(is_staff=False)
        cls.anna = make_user('anna@test.com', first_name='Anna', last_name='Klein')
        cls.raj = make_user('raj@test.com', first_name='Raj', last_name='Patel')
        for user in (cls.anna, cls.raj):
            enroll(user, cls.cohort)

    def setUp(self):
        self.pod = make_pod(self.cohort, 'RAG evals', [self.anna, self.raj])
        self.addCleanup(clear_config_cache)

    def get(self, url, token=None):
        return self.client.get(url, HTTP_AUTHORIZATION=f'Token {(token or self.token).key}')

    def artifact(self, author, title, *, meeting=None, kind='notes', body='Hello', minutes_ago=0, pod=None, **extra):
        artifact = PodArtifact.objects.create(
            pod=pod or self.pod, meeting=meeting, kind=kind, title=title, body=body,
            source=extra.pop('source', 'pasted'), added_by=author, **extra,
        )
        if minutes_ago:
            PodArtifact.objects.filter(pk=artifact.pk).update(
                created_at=timezone.now() - datetime.timedelta(minutes=minutes_ago),
            )
        artifact.refresh_from_db()
        return artifact

    def test_list_is_newest_first_without_body(self):
        held = make_meeting(self.pod, timezone.now() - datetime.timedelta(days=2), status='held')
        older = self.artifact(self.anna, 'Kickoff', minutes_ago=30)
        newer = self.artifact(
            self.raj, 'Zoom', meeting=held, kind='transcript', body='Grüße',
            source='uploaded', original_filename='zoom.vtt',
        )
        data = self.get(f'/api/pods/{self.pod.pk}/artifacts').json()
        self.assertEqual(data['total'], 2)
        self.assertEqual([a['id'] for a in data['artifacts']], [newer.pk, older.pk])
        self.assertEqual(data['artifacts'][0], {
            'id': newer.pk,
            'pod_id': self.pod.pk,
            'meeting_id': held.pk,
            'meeting_number': 1,
            'kind': 'transcript',
            'title': 'Zoom',
            'source': 'uploaded',
            'original_filename': 'zoom.vtt',
            'added_by_email': 'raj@test.com',
            'added_by_name': 'Raj P.',
            'created_at': isoformat_or_none(newer.created_at),
            'size_bytes': 7,
        })
        self.assertIsNone(data['artifacts'][1]['meeting_id'])
        self.assertIsNone(data['artifacts'][1]['meeting_number'])

    def test_meeting_number_is_null_for_a_cancelled_meeting(self):
        cancelled = make_meeting(self.pod, timezone.now() - datetime.timedelta(days=2), status='cancelled')
        self.artifact(self.anna, 'Notes', meeting=cancelled)
        item = self.get(f'/api/pods/{self.pod.pk}/artifacts').json()['artifacts'][0]
        self.assertEqual((item['meeting_id'], item['meeting_number']), (cancelled.pk, None))

    def test_filters_and_pagination(self):
        held = make_meeting(self.pod, timezone.now() - datetime.timedelta(days=2), status='held')
        self.artifact(self.anna, 'A', minutes_ago=30)
        self.artifact(self.raj, 'B', kind='transcript', meeting=held, minutes_ago=20)
        self.artifact(self.anna, 'C', meeting=held, minutes_ago=10)
        url = f'/api/pods/{self.pod.pk}/artifacts'

        def titles(query):
            return [a['title'] for a in self.get(f'{url}?{query}').json()['artifacts']]

        self.assertEqual(titles('kind=transcript'), ['B'])
        self.assertEqual(titles(f'meeting_id={held.pk}'), ['C', 'B'])
        self.assertEqual(titles(f'kind=notes&meeting_id={held.pk}'), ['C'])
        page = self.get(f'{url}?limit=1&offset=1').json()
        self.assertEqual((page['total'], page['limit'], page['offset']), (3, 1, 1))
        self.assertEqual([a['title'] for a in page['artifacts']], ['B'])
        self.assertEqual(self.get(f'{url}?limit=200').json()['limit'], 200)
        for query in ('limit=201', 'limit=0', 'offset=-1', 'kind=pdf', 'meeting_id=abc'):
            response = self.get(f'{url}?{query}')
            self.assertEqual(response.status_code, 422, query)
            self.assertEqual(response.json()['code'], 'validation_error')

    def test_detail_returns_the_body(self):
        artifact = self.artifact(self.anna, 'Kickoff', body='Line one\nLine <b>two</b>')
        data = self.get(f'/api/pods/{self.pod.pk}/artifacts/{artifact.pk}').json()
        self.assertEqual(data['body'], 'Line one\nLine <b>two</b>')
        self.assertEqual((data['id'], data['title'], data['added_by_name']), (artifact.pk, 'Kickoff', 'Anna K.'))

    def test_another_pods_artifact_is_unknown_artifact(self):
        other = make_pod(self.cohort, 'Other', [])
        artifact = self.artifact(self.anna, 'Elsewhere', pod=other)
        response = self.get(f'/api/pods/{self.pod.pk}/artifacts/{artifact.pk}')
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()['code'], 'unknown_artifact')
        self.assertEqual(self.get('/api/pods/999999/artifacts').json()['code'], 'unknown_pod')

    def test_pod_detail_counts_artifacts(self):
        self.artifact(self.anna, 'A')
        self.artifact(self.raj, 'B')
        self.assertEqual(self.get(f'/api/pods/{self.pod.pk}').json()['artifact_count'], 2)

    def test_works_with_the_member_kill_switch_off(self):
        IntegrationSetting.objects.create(key='PODS_ARTIFACTS_ENABLED', value='false')
        clear_config_cache()
        artifact = self.artifact(self.anna, 'A')
        self.assertEqual(self.get(f'/api/pods/{self.pod.pk}/artifacts').json()['total'], 1)
        self.assertEqual(self.get(f'/api/pods/{self.pod.pk}/artifacts/{artifact.pk}').json()['id'], artifact.pk)

    def test_requires_a_staff_token(self):
        artifact = self.artifact(self.anna, 'A')
        for url in (f'/api/pods/{self.pod.pk}/artifacts', f'/api/pods/{self.pod.pk}/artifacts/{artifact.pk}'):
            self.assertEqual(self.client.get(url).status_code, 401, url)
            self.assertIn(self.get(url, token=self.member_token).status_code, (401, 403), url)
            self.assertEqual(self.client.post(url, HTTP_AUTHORIZATION=f'Token {self.token.key}').status_code, 405)
