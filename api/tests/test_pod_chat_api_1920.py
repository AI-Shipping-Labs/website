"""Staff pods API: chat transcript and moderation (issue #1920)."""

import datetime

from django.test import TestCase, tag
from django.utils import timezone

from accounts.models import Token
from api.serializers.datetime import isoformat_or_none
from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting
from pods.models import PodMessage
from pods.tests.fixtures import enroll, make_cohort, make_course, make_pod, make_user


@tag('core')
class PodChatApiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
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

    def call(self, method, url, token=None):
        return getattr(self.client, method)(url, HTTP_AUTHORIZATION=f'Token {(token or self.token).key}')

    def msg(self, author, body, minutes_ago=0, pod=None):
        message = PodMessage.objects.create(pod=pod or self.pod, author=author, body=body)
        if minutes_ago:
            PodMessage.objects.filter(pk=message.pk).update(
                created_at=timezone.now() - datetime.timedelta(minutes=minutes_ago),
            )
        return message

    def test_list_is_oldest_first_includes_hidden_and_pages(self):
        first = self.msg(self.anna, 'one', minutes_ago=30)
        first.refresh_from_db()
        hidden = self.msg(self.raj, 'two', minutes_ago=20)
        self.msg(self.anna, 'three', minutes_ago=10)
        self.call('post', f'/api/pods/{self.pod.pk}/messages/{hidden.pk}/hide')
        data = self.call('get', f'/api/pods/{self.pod.pk}/messages').json()
        self.assertEqual(data['total'], 3)
        self.assertEqual([m['body'] for m in data['messages']], ['one', 'two', 'three'])
        self.assertEqual(data['messages'][0], {
            'id': first.pk,
            'author_email': 'anna@test.com',
            'author_name': 'Anna K.',
            'body': 'one',
            'created_at': isoformat_or_none(first.created_at),
            'hidden': False,
            'hidden_at': None,
            'hidden_by_email': None,
        })
        self.assertTrue(data['messages'][1]['hidden'])
        self.assertEqual(data['messages'][1]['hidden_by_email'], 'staff@test.com')
        page = self.call('get', f'/api/pods/{self.pod.pk}/messages?limit=1&offset=1').json()
        self.assertEqual((page['total'], page['limit'], page['offset']), (3, 1, 1))
        self.assertEqual([m['body'] for m in page['messages']], ['two'])

    def test_limit_over_200_is_422(self):
        self.assertEqual(self.call('get', f'/api/pods/{self.pod.pk}/messages?limit=201').status_code, 422)
        self.assertEqual(self.call('get', f'/api/pods/{self.pod.pk}/messages?limit=200').json()['limit'], 200)

    def test_hide_and_restore_are_idempotent_and_return_the_message(self):
        message = self.msg(self.raj, 'buy crypto')
        url = f'/api/pods/{self.pod.pk}/messages/{message.pk}'
        first = self.call('post', f'{url}/hide')
        self.assertEqual((first.json()['id'], first.json()['hidden']), (message.pk, True))
        self.assertEqual(first.json()['hidden_by_email'], 'staff@test.com')
        again = self.call('post', f'{url}/hide').json()
        self.assertEqual(again['hidden_at'], first.json()['hidden_at'])
        restored = self.call('post', f'{url}/restore')
        self.assertEqual(restored.json()['id'], message.pk)
        self.assertEqual(
            {k: restored.json()[k] for k in ('hidden', 'hidden_at', 'hidden_by_email')},
            {'hidden': False, 'hidden_at': None, 'hidden_by_email': None},
        )
        self.assertFalse(self.call('post', f'{url}/restore').json()['hidden'])

    def test_unknown_pod_and_other_pods_message_are_structured_404(self):
        other = make_pod(self.cohort, 'Other', [self.anna])
        foreign = self.msg(self.anna, 'elsewhere', pod=other)
        response = self.call('post', f'/api/pods/{self.pod.pk}/messages/{foreign.pk}/hide')
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()['code'], 'unknown_message')
        foreign.refresh_from_db()
        self.assertIsNone(foreign.hidden_at)
        for method, url in (
            ('get', '/api/pods/999999/messages'),
            ('post', f'/api/pods/999999/messages/{foreign.pk}/hide'),
            ('post', f'/api/pods/999999/messages/{foreign.pk}/restore'),
        ):
            response = self.call(method, url)
            self.assertEqual(response.status_code, 404, url)
            self.assertEqual(response.json()['code'], 'unknown_pod')

    def test_endpoints_require_a_staff_token(self):
        message = self.msg(self.raj, 'x')
        for method, url in (
            ('get', f'/api/pods/{self.pod.pk}/messages'),
            ('post', f'/api/pods/{self.pod.pk}/messages/{message.pk}/hide'),
            ('post', f'/api/pods/{self.pod.pk}/messages/{message.pk}/restore'),
        ):
            self.assertEqual(getattr(self.client, method)(url).status_code, 401, url)
            self.assertIn(self.call(method, url, token=self.member_token).status_code, (401, 403), url)
        message.refresh_from_db()
        self.assertIsNone(message.hidden_at)

    def test_pod_detail_counts_visible_messages_only(self):
        detail = self.call('get', f'/api/pods/{self.pod.pk}').json()
        self.assertEqual((detail['message_count'], detail['last_message_at']), (0, None))
        visible = self.msg(self.anna, 'visible', minutes_ago=10)
        hidden = self.msg(self.raj, 'hidden later')
        self.call('post', f'/api/pods/{self.pod.pk}/messages/{hidden.pk}/hide')
        detail = self.call('get', f'/api/pods/{self.pod.pk}').json()
        self.assertEqual(detail['message_count'], 1)
        visible.refresh_from_db()
        self.assertEqual(detail['last_message_at'], isoformat_or_none(visible.created_at))

    def test_api_works_with_chat_switched_off(self):
        IntegrationSetting.objects.update_or_create(key='PODS_CHAT_ENABLED', defaults={'value': 'false'})
        clear_config_cache()
        message = self.msg(self.raj, 'x')
        self.assertEqual(self.call('get', f'/api/pods/{self.pod.pk}/messages').json()['total'], 1)
        self.assertTrue(self.call('post', f'/api/pods/{self.pod.pk}/messages/{message.pk}/hide').json()['hidden'])
