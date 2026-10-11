"""Studio pod chat transcript and moderation (issue #1920)."""

import datetime
import re

from django.test import TestCase, override_settings, tag
from django.utils import timezone

from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting
from pods.models import PodMessage
from pods.tests.fixtures import COURSE_SLUG, enroll, make_cohort, make_course, make_pod, make_user
from pods.tests.test_member_views import texts


@tag('core')
class StudioPodChatTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = make_course()
        cls.cohort = make_cohort(cls.course)
        cls.staff = make_user('admin@test.com', first_name='Sam', last_name='Staff', staff=True)
        cls.anna = make_user('anna@test.com', first_name='Anna', last_name='Klein')
        cls.raj = make_user('raj@test.com', first_name='Raj', last_name='Patel')
        for user in (cls.anna, cls.raj):
            enroll(user, cls.cohort)

    def setUp(self):
        self.pod = make_pod(self.cohort, 'RAG evals', [self.anna, self.raj], owner=self.anna)
        self.client.force_login(self.staff)
        self.addCleanup(clear_config_cache)

    def url(self):
        return f'/studio/pods/{self.pod.pk}/'

    def msg(self, author, body, minutes_ago=0):
        message = PodMessage.objects.create(pod=self.pod, author=author, body=body)
        if minutes_ago:
            PodMessage.objects.filter(pk=message.pk).update(
                created_at=timezone.now() - datetime.timedelta(minutes=minutes_ago),
            )
        return message

    def test_chat_section_after_meetings_with_counts_names_and_emails(self):
        self.msg(self.anna, 'Hello pod', minutes_ago=30)
        crypto = self.msg(self.raj, 'buy crypto at example.com', minutes_ago=20)
        self.msg(self.anna, 'Line one\nline <b>two</b>', minutes_ago=10)
        PodMessage.objects.filter(pk=crypto.pk).update(hidden_at=timezone.now())
        response = self.client.get(self.url())
        html = response.content.decode()
        self.assertLess(html.index('data-testid="studio-pod-meetings"'), html.index('data-testid="studio-pod-chat"'))
        self.assertEqual(texts(response, 'studio-pod-chat-heading'), ['Chat (3 messages, 1 hidden)'])
        self.assertEqual(
            texts(response, 'studio-pod-chat-body'),
            ['Hello pod', 'buy crypto at example.com', 'Line one line <b>two</b>'],
        )
        self.assertIn('line &lt;b&gt;two&lt;/b&gt;', html)
        authors = texts(response, 'studio-pod-chat-author')
        self.assertEqual(authors[1], 'Raj P. raj@test.com')

    def test_hide_then_restore_round_trip(self):
        message = self.msg(self.raj, 'buy crypto at example.com')
        response = self.client.post(f'/studio/pods/{self.pod.pk}/messages/{message.pk}/hide', follow=True)
        self.assertEqual(response.redirect_chain[-1][0], f'/studio/pods/{self.pod.pk}/#chat')
        self.assertContains(response, 'Message hidden.')
        message.refresh_from_db()
        self.assertIsNotNone(message.hidden_at)
        self.assertEqual(message.hidden_by, self.staff)
        hidden = texts(response, 'studio-pod-chat-hidden')[0]
        self.assertTrue(hidden.startswith('Hidden Hidden by Sam S. on '), hidden)
        self.assertEqual(texts(response, 'studio-pod-chat-heading'), ['Chat (1 message, 1 hidden)'])
        self.assertContains(response, 'data-testid="studio-pod-chat-restore"')
        self.assertNotContains(response, 'data-testid="studio-pod-chat-hide"')

        # Idempotent: hiding again keeps the first hide.
        first_hidden_at = message.hidden_at
        self.client.post(f'/studio/pods/{self.pod.pk}/messages/{message.pk}/hide')
        message.refresh_from_db()
        self.assertEqual(message.hidden_at, first_hidden_at)

        with override_settings(PODS_COURSE_SLUGS=COURSE_SLUG):
            self.client.force_login(self.anna)
            member_page = self.client.get(f'/courses/{COURSE_SLUG}/home/pods/{self.pod.pk}?cohort=4')
            self.assertNotContains(member_page, 'buy crypto')
            self.client.force_login(self.staff)
            response = self.client.post(
                f'/studio/pods/{self.pod.pk}/messages/{message.pk}/restore', follow=True,
            )
            self.assertContains(response, 'Message restored.')
            message.refresh_from_db()
            self.assertIsNone(message.hidden_at)
            self.assertIsNone(message.hidden_by)
            self.client.post(f'/studio/pods/{self.pod.pk}/messages/{message.pk}/restore')
            self.client.force_login(self.anna)
            member_page = self.client.get(f'/courses/{COURSE_SLUG}/home/pods/{self.pod.pk}?cohort=4')
            self.assertEqual(texts(member_page, 'pod-chat-body'), ['buy crypto at example.com'])

    def test_non_staff_cannot_hide_or_restore(self):
        message = self.msg(self.raj, 'keep me')
        self.client.force_login(self.anna)
        for action in ('hide', 'restore'):
            response = self.client.post(f'/studio/pods/{self.pod.pk}/messages/{message.pk}/{action}')
            self.assertEqual(response.status_code, 403)
        message.refresh_from_db()
        self.assertIsNone(message.hidden_at)

    def test_message_of_another_pod_is_404(self):
        other = make_pod(self.cohort, 'Other', [self.anna])
        message = PodMessage.objects.create(pod=other, author=self.anna, body='elsewhere')
        response = self.client.post(f'/studio/pods/{self.pod.pk}/messages/{message.pk}/hide')
        self.assertEqual(response.status_code, 404)
        message.refresh_from_db()
        self.assertIsNone(message.hidden_at)

    def test_empty_chat(self):
        response = self.client.get(self.url())
        self.assertEqual(texts(response, 'studio-pod-chat-heading'), ['Chat (0 messages, 0 hidden)'])
        self.assertIn('No messages yet.', texts(response, 'studio-pod-chat-empty')[0])

    def test_over_200_messages_shows_latest_200(self):
        PodMessage.objects.bulk_create([
            PodMessage(pod=self.pod, author=self.anna, body=f'msg {i}') for i in range(201)
        ])
        base = timezone.now() - datetime.timedelta(days=1)
        for index, pk in enumerate(PodMessage.objects.order_by('pk').values_list('pk', flat=True)):
            PodMessage.objects.filter(pk=pk).update(created_at=base + datetime.timedelta(minutes=index))
        response = self.client.get(self.url())
        bodies = texts(response, 'studio-pod-chat-body')
        self.assertEqual(len(bodies), 200)
        self.assertEqual((bodies[0], bodies[-1]), ('msg 1', 'msg 200'))
        self.assertEqual(
            texts(response, 'studio-pod-chat-truncated'),
            ['Showing the latest 200 of 201 messages. Use the staff API for the full transcript.'],
        )

    def test_studio_chat_works_with_chat_switched_off(self):
        IntegrationSetting.objects.update_or_create(key='PODS_CHAT_ENABLED', defaults={'value': 'false'})
        clear_config_cache()
        message = self.msg(self.raj, 'still moderated')
        response = self.client.get(self.url())
        self.assertEqual(texts(response, 'studio-pod-chat-body'), ['still moderated'])
        self.client.post(f'/studio/pods/{self.pod.pk}/messages/{message.pk}/hide')
        message.refresh_from_db()
        self.assertIsNotNone(message.hidden_at)
        self.assertTrue(re.search(r'Chat \(1 message, 1 hidden\)', self.client.get(self.url()).content.decode()))
