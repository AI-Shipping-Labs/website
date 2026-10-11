"""Pod chat: member page, posting, unread line, kill switch, settings (issue #1920)."""

import datetime
import re

from django.core import mail
from django.db import connection
from django.test import TestCase, override_settings, tag
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting
from notifications.models import Notification
from pods.models import PodJoinRequest, PodMembership, PodMessage
from pods.services import chat as chat_svc
from pods.services import config as pods_config
from pods.tests.fixtures import COURSE_SLUG, enroll, make_cohort, make_course, make_pod, make_user
from pods.tests.test_member_views import texts

ENABLED = override_settings(PODS_COURSE_SLUGS=COURSE_SLUG)
EMAIL_RE = re.compile(r'[\w.+-]+@[\w-]+\.[\w.]+')
NOTICE = 'Admins can read this chat. Do not share private details. Slack is better for ongoing conversation.'
READ_ONLY = 'You can read this chat. Only pod members can post.'
INVITE = 'Introduce yourself: what are you building and what is your timezone?'


def _message(pod, author, body, *, minutes_ago=0, hidden=False):
    message = PodMessage.objects.create(pod=pod, author=author, body=body)
    if minutes_ago:
        PodMessage.objects.filter(pk=message.pk).update(
            created_at=timezone.now() - datetime.timedelta(minutes=minutes_ago),
        )
    if hidden:
        PodMessage.objects.filter(pk=message.pk).update(hidden_at=timezone.now())
    return message


class ChatFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = make_course()
        cls.cohort = make_cohort(cls.course)
        cls.anna = make_user('anna@test.com', first_name='Anna', last_name='Klein', timezone_name='Europe/Berlin')
        cls.raj = make_user('raj@test.com', first_name='Raj', last_name='Patel')
        cls.mike = make_user('mike@test.com', first_name='Mike', last_name='Kowalski')
        cls.waiter = make_user('wait@test.com', first_name='Wanda')
        cls.staff = make_user('staff@test.com', first_name='Sam', staff=True)
        for user in (cls.anna, cls.raj, cls.mike, cls.waiter):
            enroll(user, cls.cohort)

    def setUp(self):
        self.pod = make_pod(self.cohort, 'RAG evals', [self.anna, self.raj], owner=self.anna)
        self.addCleanup(clear_config_cache)
        clear_config_cache()

    def _set(self, key, value):
        IntegrationSetting.objects.update_or_create(key=key, defaults={'value': value})
        clear_config_cache()

    def pod_url(self, pod=None):
        pod = pod or self.pod
        return f'/courses/{COURSE_SLUG}/home/pods/{pod.pk}?cohort=4'

    def post_url(self, pod=None):
        pod = pod or self.pod
        return f'/courses/{COURSE_SLUG}/home/pods/{pod.pk}/messages'

    def list_url(self):
        return f'/courses/{COURSE_SLUG}/home/pods?cohort=4'

    def chat_html(self, response):
        html = response.content.decode()
        start = html.find('data-testid="pod-chat"')
        self.assertNotEqual(start, -1, 'chat section missing')
        return html[start:html.find('</section>', start)]


@tag('core')
@ENABLED
class MemberChatPageTest(ChatFixture):
    def test_member_sees_chat_as_the_last_section(self):
        self.client.force_login(self.anna)
        response = self.client.get(self.pod_url())
        html = response.content.decode()
        self.assertIn('id="chat"', html)
        chat_at = html.index('data-testid="pod-chat"')
        for earlier in ('data-testid="pod-members"', 'data-testid="pod-my-availability"'):
            self.assertLess(html.index(earlier), chat_at)
        article_end = html.index('</article>', chat_at)
        self.assertEqual(html.count('<section', chat_at, article_end), 0)

    def test_empty_chat_shows_invite_notice_and_composer(self):
        self.client.force_login(self.anna)
        response = self.client.get(self.pod_url())
        empty = texts(response, 'pod-chat-empty')[0]
        self.assertIn('No messages yet', empty)
        self.assertIn(INVITE, empty)
        self.assertEqual(texts(response, 'pod-chat-notice'), [NOTICE])
        chat = self.chat_html(response)
        self.assertIn(f'placeholder="{INVITE}"', chat)
        self.assertIn('maxlength="1000"', chat)
        self.assertIn('name="body"', chat)
        # The notice sits directly above the composer, with no dismiss control.
        notice_end = chat.index('</p>', chat.index('data-testid="pod-chat-notice"'))
        self.assertEqual(chat[notice_end + 4:].lstrip()[:5], '<form')
        self.assertNotIn('dismiss', chat.lower())

    def test_messages_render_oldest_first_with_name_you_and_local_time(self):
        _message(self.pod, self.raj, 'First from Raj', minutes_ago=10)
        _message(self.pod, self.anna, 'Second from Anna', minutes_ago=5)
        self.client.force_login(self.anna)
        response = self.client.get(self.pod_url())
        self.assertEqual(texts(response, 'pod-chat-body'), ['First from Raj', 'Second from Anna'])
        self.assertEqual(texts(response, 'pod-chat-author'), ['Raj P.', 'Anna K. (you)'])
        for label in texts(response, 'pod-chat-time'):
            self.assertTrue(label.endswith('Europe/Berlin'), label)
        chat = self.chat_html(response)
        self.assertIsNone(EMAIL_RE.search(chat))
        self.assertNotIn('placeholder=', chat)
        self.assertEqual(texts(response, 'pod-chat-empty'), [])

    def test_body_is_escaped_links_are_nofollow_and_markdown_is_literal(self):
        _message(self.pod, self.raj, '<script>alert(1)</script>')
        _message(self.pod, self.raj, 'see https://github.com/raj/evals now')
        _message(self.pod, self.raj, '**x**\nsecond line')
        self.client.force_login(self.anna)
        chat = self.chat_html(self.client.get(self.pod_url()))
        self.assertNotIn('<script>alert(1)</script>', chat)
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;', chat)
        link = re.search(r'<a [^>]*href="https://github.com/raj/evals"[^>]*>', chat)
        self.assertIsNotNone(link)
        self.assertIn('nofollow', re.search(r'rel="([^"]*)"', link.group(0)).group(1))
        self.assertIn('**x**\nsecond line', chat)
        self.assertNotIn('<strong>', chat)

    def test_only_latest_50_visible_messages_render(self):
        for index in range(52):
            _message(self.pod, self.raj, f'msg {index}', minutes_ago=200 - index)
        _message(self.pod, self.raj, 'hidden newest', hidden=True)
        self.client.force_login(self.anna)
        response = self.client.get(self.pod_url())
        bodies = texts(response, 'pod-chat-body')
        self.assertEqual(len(bodies), 50)
        self.assertEqual(bodies[0], 'msg 2')
        self.assertEqual(bodies[-1], 'msg 51')
        self.assertEqual(texts(response, 'pod-chat-truncated'), ['Showing the latest 50 messages.'])

    def test_exactly_50_messages_show_no_truncation_line(self):
        for index in range(50):
            _message(self.pod, self.raj, f'msg {index}', minutes_ago=100 - index)
        self.client.force_login(self.anna)
        response = self.client.get(self.pod_url())
        self.assertEqual(len(texts(response, 'pod-chat-body')), 50)
        self.assertEqual(texts(response, 'pod-chat-truncated'), [])

    def test_refresh_link_reloads_the_chat_section(self):
        self.client.force_login(self.anna)
        chat = self.chat_html(self.client.get(self.pod_url()))
        href = re.search(r'<a href="([^"]+)"[^>]*data-testid="pod-chat-refresh"', chat).group(1)
        # A bare ``#chat`` link only scrolls; the query forces a fresh load.
        self.assertRegex(href, rf'^/courses/{COURSE_SLUG}/home/pods/{self.pod.pk}\?refresh=\d+#chat$')

    def test_hidden_messages_never_render(self):
        _message(self.pod, self.raj, 'visible one')
        _message(self.pod, self.raj, 'buy crypto at example.com', hidden=True)
        self.client.force_login(self.anna)
        response = self.client.get(self.pod_url())
        self.assertEqual(texts(response, 'pod-chat-body'), ['visible one'])
        self.assertNotContains(response, 'buy crypto')

    def test_slack_link_shows_moved_line_and_keeps_composer(self):
        self.pod.slack_channel_url = 'https://team.slack.com/archives/C0123'
        self.pod.save()
        self.client.force_login(self.raj)
        response = self.client.get(self.pod_url())
        self.assertEqual(texts(response, 'pod-chat-slack'), ['This pod has moved to Slack. Open in Slack'])
        chat = self.chat_html(response)
        link = re.search(r'<a [^>]*data-testid="pod-chat-slack-open"[^>]*>', chat).group(0)
        self.assertIn('href="https://team.slack.com/archives/C0123"', link)
        self.assertIn('target="_blank"', link)
        self.assertIn('data-testid="pod-chat-form"', chat)

    def test_without_slack_link_no_moved_line(self):
        self.client.force_login(self.raj)
        self.assertEqual(texts(self.client.get(self.pod_url()), 'pod-chat-slack'), [])

    def test_opening_the_page_marks_the_chat_read(self):
        self.client.force_login(self.anna)
        before = timezone.now()
        self.client.get(self.pod_url())
        read_at = PodMembership.objects.get(pod=self.pod, user=self.anna).chat_read_at
        self.assertIsNotNone(read_at)
        self.assertGreaterEqual(read_at, before)
        self.assertIsNone(PodMembership.objects.get(pod=self.pod, user=self.raj).chat_read_at)

    def test_former_member_message_still_renders_under_their_name(self):
        _message(self.pod, self.raj, 'bye all')
        PodMembership.objects.filter(pod=self.pod, user=self.raj).delete()
        self.client.force_login(self.anna)
        response = self.client.get(self.pod_url())
        self.assertEqual(texts(response, 'pod-chat-author'), ['Raj P.'])
        self.assertEqual(texts(response, 'pod-chat-body'), ['bye all'])

    def test_closed_pod_members_can_still_chat(self):
        self.pod.status = 'closed'
        self.pod.save()
        self.client.force_login(self.anna)
        self.assertContains(self.client.get(self.pod_url()), 'data-testid="pod-chat-form"')
        response = self.client.post(self.post_url(), {'body': 'still here'})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(PodMessage.objects.filter(pod=self.pod, body='still here').exists())


@tag('core')
@ENABLED
class ChatVisibilityTest(ChatFixture):
    def setUp(self):
        super().setUp()
        _message(self.pod, self.raj, 'secret pod plans')

    def test_non_member_requester_and_waitlisted_see_no_chat(self):
        PodJoinRequest.objects.create(pod=self.pod, user=self.waiter, status='waitlisted')
        third = make_user('req@test.com', first_name='Rita')
        enroll(third, self.cohort)
        PodJoinRequest.objects.create(pod=self.pod, user=third, status='pending')
        for viewer in (self.mike, self.waiter, third):
            self.client.force_login(viewer)
            response = self.client.get(self.pod_url())
            self.assertContains(response, 'data-testid="pod-purpose"')
            self.assertNotContains(response, 'data-testid="pod-chat"')
            self.assertNotContains(response, 'secret pod plans')

    def test_former_member_sees_no_chat(self):
        PodMembership.objects.filter(pod=self.pod, user=self.raj).delete()
        self.client.force_login(self.raj)
        response = self.client.get(self.pod_url())
        self.assertNotContains(response, 'data-testid="pod-chat"')
        self.assertNotContains(response, 'secret pod plans')

    def test_staff_non_member_reads_without_composer(self):
        self.client.force_login(self.staff)
        response = self.client.get(self.pod_url())
        self.assertEqual(texts(response, 'pod-chat-body'), ['secret pod plans'])
        self.assertEqual(texts(response, 'pod-chat-notice'), [NOTICE])
        self.assertEqual(texts(response, 'pod-chat-read-only'), [READ_ONLY])
        self.assertNotContains(response, 'data-testid="pod-chat-form"')

    def test_staff_reads_archived_pod_chat(self):
        self.pod.status = 'archived'
        self.pod.save()
        self.client.force_login(self.staff)
        response = self.client.get(self.pod_url())
        self.assertEqual(texts(response, 'pod-chat-body'), ['secret pod plans'])
        self.client.force_login(self.anna)
        self.assertEqual(self.client.get(self.pod_url()).status_code, 404)


@tag('core')
@ENABLED
class ChatPostTest(ChatFixture):
    def test_member_post_creates_message_and_redirects_to_chat(self):
        self.client.force_login(self.anna)
        response = self.client.post(self.post_url(), {'body': '  Hi, Berlin time  '})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response['Location'].endswith('#chat'))
        self.assertIn(f'/home/pods/{self.pod.pk}', response['Location'])
        message = PodMessage.objects.get(pod=self.pod)
        self.assertEqual((message.author, message.body), (self.anna, 'Hi, Berlin time'))
        self.assertIsNotNone(PodMembership.objects.get(pod=self.pod, user=self.anna).chat_read_at)

    def test_non_member_staff_and_former_member_get_403(self):
        former = make_user('former@test.com', first_name='Fay')
        enroll(former, self.cohort)
        PodMembership.objects.create(pod=self.pod, user=former, source='staff')
        PodMembership.objects.filter(pod=self.pod, user=former).delete()
        for viewer in (self.mike, self.staff, former):
            self.client.force_login(viewer)
            response = self.client.post(self.post_url(), {'body': 'let me in'})
            self.assertEqual(response.status_code, 403, viewer.email)
            self.assertEqual(response.content.decode(), 'Only pod members can post in this chat.')
        self.assertFalse(PodMessage.objects.exists())

    def test_anonymous_post_redirects_to_login(self):
        response = self.client.post(self.post_url(), {'body': 'hello'})
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])
        self.assertFalse(PodMessage.objects.exists())

    def _follow_error(self, body):
        self.client.force_login(self.anna)
        return self.client.post(self.post_url(), {'body': body}, follow=True)

    def test_whitespace_body_is_rejected(self):
        response = self._follow_error('   \n  ')
        self.assertEqual(texts(response, 'pod-chat-error'), ['Write a message first.'])
        self.assertFalse(PodMessage.objects.exists())

    def test_1001_characters_rejected_and_draft_kept(self):
        body = 'a' * 1001
        response = self._follow_error(body)
        self.assertEqual(texts(response, 'pod-chat-error'), ['Messages can be up to 1000 characters.'])
        self.assertIn(f'>{body}</textarea>', self.chat_html(response))
        self.assertFalse(PodMessage.objects.exists())
        # The error shows once: a reload clears it.
        self.assertEqual(texts(self.client.get(self.pod_url()), 'pod-chat-error'), [])

    def test_exactly_1000_characters_after_strip_is_accepted(self):
        self.client.force_login(self.anna)
        self.client.post(self.post_url(), {'body': '  ' + 'b' * 1000 + '  '})
        self.assertEqual(len(PodMessage.objects.get().body), 1000)

    def test_hourly_limit_counts_across_pods_and_ignores_older_messages(self):
        self._set('PODS_CHAT_MESSAGES_PER_HOUR', '2')
        other = make_pod(self.cohort, 'Other pod', [self.anna])
        _message(other, self.anna, 'elsewhere', minutes_ago=10)
        _message(self.pod, self.anna, 'long ago', minutes_ago=61)
        self.client.force_login(self.anna)
        self.client.post(self.post_url(), {'body': 'second this hour'})
        self.assertTrue(PodMessage.objects.filter(body='second this hour').exists())
        response = self.client.post(self.post_url(), {'body': 'one more thing'}, follow=True)
        self.assertEqual(
            texts(response, 'pod-chat-error'),
            ['You are sending messages quickly. Try again in a few minutes.'],
        )
        self.assertIn('>one more thing</textarea>', self.chat_html(response))
        self.assertFalse(PodMessage.objects.filter(body='one more thing').exists())

    def test_posting_sends_no_notification_email_or_slack(self):
        self.client.force_login(self.anna)
        before = Notification.objects.count()
        self.client.post(self.post_url(), {'body': 'quiet message'})
        self.assertEqual(Notification.objects.count(), before)
        self.assertEqual(mail.outbox, [])


    def test_service_refuses_to_post_when_chat_is_switched_off(self):
        self._set('PODS_CHAT_ENABLED', 'false')
        with self.assertRaises(chat_svc.ChatError) as raised:
            chat_svc.post_message(self.pod, self.anna, 'hello')
        self.assertEqual(raised.exception.code, chat_svc.CODE_DISABLED)
        self.assertFalse(PodMessage.objects.exists())


@tag('core')
@ENABLED
class UnreadLineTest(ChatFixture):
    def _row_unread(self, response, pod):
        html = response.content.decode()
        start = html.index(f'data-pod-id="{pod.pk}"')
        row = html[start:html.index('</li>', start)]
        match = re.search(r'data-testid="pod-row-unread"[^>]*>\s*([^<]+?)\s*<', row)
        return match.group(1) if match else ''

    def _read(self, user, minutes_ago):
        PodMembership.objects.filter(pod=self.pod, user=user).update(
            chat_read_at=timezone.now() - datetime.timedelta(minutes=minutes_ago),
        )

    def test_counts_other_members_messages_after_read(self):
        self._read(self.raj, 60)
        _message(self.pod, self.anna, 'one', minutes_ago=30)
        _message(self.pod, self.anna, 'two', minutes_ago=20)
        _message(self.pod, self.anna, 'before read', minutes_ago=90)
        _message(self.pod, self.raj, 'my own', minutes_ago=10)
        _message(self.pod, self.anna, 'hidden', minutes_ago=5, hidden=True)
        self.client.force_login(self.raj)
        self.assertEqual(self._row_unread(self.client.get(self.list_url()), self.pod), '2 new messages')

    def test_singular_and_cleared_after_opening_pod(self):
        self._read(self.raj, 60)
        _message(self.pod, self.anna, 'one', minutes_ago=30)
        self.client.force_login(self.raj)
        self.assertEqual(self._row_unread(self.client.get(self.list_url()), self.pod), '1 new message')
        self.client.get(self.pod_url())
        self.assertEqual(self._row_unread(self.client.get(self.list_url()), self.pod), '')

    def test_new_member_does_not_count_history_before_joining(self):
        _message(self.pod, self.anna, 'old news', minutes_ago=30)
        PodMembership.objects.create(pod=self.pod, user=self.mike, source='request')
        self.client.force_login(self.mike)
        self.assertEqual(self._row_unread(self.client.get(self.list_url()), self.pod), '')
        _message(self.pod, self.anna, 'welcome Mike')
        self.assertEqual(self._row_unread(self.client.get(self.list_url()), self.pod), '1 new message')

    def test_non_member_rows_never_show_unread(self):
        _message(self.pod, self.anna, 'hello')
        self.client.force_login(self.mike)
        response = self.client.get(self.list_url())
        self.assertNotContains(response, 'data-testid="pod-row-unread"')

    def test_unread_counts_is_one_query_for_any_number_of_pods(self):
        pods = [self.pod] + [make_pod(self.cohort, f'Pod {i}', [self.raj, self.anna]) for i in range(2)]
        for pod in pods:
            _message(pod, self.anna, 'hi')
        with self.assertNumQueries(1):
            one = chat_svc.unread_counts([self.pod.pk], self.raj)
        with self.assertNumQueries(1):
            three = chat_svc.unread_counts([p.pk for p in pods], self.raj)
        self.assertEqual(one, {self.pod.pk: 1})
        self.assertEqual(three, {p.pk: 1 for p in pods})

    def test_pods_tab_query_count_does_not_grow_with_member_pods(self):
        self.client.force_login(self.raj)
        _message(self.pod, self.anna, 'hi')
        self.client.get(self.list_url())  # warm per-session caches

        def count_queries():
            with CaptureQueriesContext(connection) as ctx:
                response = self.client.get(self.list_url())
            self.assertContains(response, 'data-testid="pods-rows"')
            return len(ctx.captured_queries)

        with_one = count_queries()
        for index in range(2):
            pod = make_pod(self.cohort, f'Extra {index}', [self.raj, self.anna])
            _message(pod, self.anna, 'hi')
        with_three = count_queries()
        self.assertEqual(with_one, with_three)
        self.assertEqual(
            texts(self.client.get(self.list_url()), 'pod-row-unread'),
            ['1 new message', '1 new message', '1 new message'],
        )


@tag('core')
@ENABLED
class KillSwitchTest(ChatFixture):
    def test_chat_off_hides_section_unread_and_404s_post(self):
        _message(self.pod, self.raj, 'hello')
        self._set('PODS_CHAT_ENABLED', 'false')
        self.client.force_login(self.anna)
        response = self.client.get(self.pod_url())
        self.assertNotContains(response, 'data-testid="pod-chat"')
        self.assertNotContains(response, 'hello')
        self.assertIsNone(PodMembership.objects.get(pod=self.pod, user=self.anna).chat_read_at)
        self.assertNotContains(self.client.get(self.list_url()), 'data-testid="pod-row-unread"')
        self.assertEqual(self.client.post(self.post_url(), {'body': 'x'}).status_code, 404)
        self.assertEqual(PodMessage.objects.count(), 1)


@tag('core')
class ChatSettingsTest(TestCase):
    def setUp(self):
        self.addCleanup(clear_config_cache)
        clear_config_cache()

    def _set(self, key, value):
        IntegrationSetting.objects.update_or_create(key=key, defaults={'value': value})
        clear_config_cache()

    def test_defaults(self):
        self.assertTrue(pods_config.chat_enabled())
        self.assertEqual(pods_config.chat_messages_per_hour(), 30)

    def test_invalid_hourly_limit_falls_back_to_30(self):
        for raw in ('abc', '0', '501', '-3'):
            self._set('PODS_CHAT_MESSAGES_PER_HOUR', raw)
            self.assertEqual(pods_config.chat_messages_per_hour(), 30, raw)
        self._set('PODS_CHAT_MESSAGES_PER_HOUR', '500')
        self.assertEqual(pods_config.chat_messages_per_hour(), 500)

    def test_both_keys_are_registered_in_the_pods_group(self):
        from integrations.settings_registry import INTEGRATION_GROUPS

        pods_group = next(group for group in INTEGRATION_GROUPS if group['name'] == 'pods')
        keys = {item['key']: item for item in pods_group['keys']}
        self.assertEqual(keys['PODS_CHAT_ENABLED']['docs_url'], '_docs/integrations/pods.md#pods_chat_enabled')
        self.assertEqual(
            keys['PODS_CHAT_MESSAGES_PER_HOUR']['docs_url'],
            '_docs/integrations/pods.md#pods_chat_messages_per_hour',
        )
