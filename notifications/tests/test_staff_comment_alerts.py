"""Staff Slack alert for member comments (issue #1926).

A non-staff comment or reply on a course lesson, homework step, or
workshop page posts one ``chat.postMessage`` to the staff channel. Staff
authors, member-owned threads, moderation, and votes never post, and a
Slack failure never affects the comment.
"""

import json
import uuid
from datetime import date, timedelta
from unittest import mock

import requests
from community_base.mail.models import EmailDelivery
from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.utils import timezone

from accounts.models import Token
from bookclub.models import Book, Chapter, Note
from comments.models import Comment
from comments.services import (
    create_comment,
    edit_comment_body,
    hide_comment,
    restore_comment,
)
from content.models import (
    Course,
    Instructor,
    Module,
    Unit,
    Workshop,
    WorkshopPage,
)
from content.models.cohort import Cohort
from content.models.homework import (
    Homework,
    HomeworkStepThread,
    Question,
    QuestionType,
)
from content.services.homework_step_threads import step_thread_content_id
from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting
from notifications.models import Notification
from plans.models import Plan, Sprint

User = get_user_model()

POST_TARGET = 'notifications.services.staff_comment_alerts.requests.post'
BASE_URL = 'https://aisl.test'


def _ok_response():
    response = mock.Mock()
    response.json.return_value = {'ok': True}
    return response


def _payload(post_mock, index=0):
    return post_mock.call_args_list[index].kwargs['json']


def _blocks_text(payload):
    """Concatenate every mrkdwn/plain text in the Block Kit payload."""
    parts = []
    for block in payload['blocks']:
        if 'text' in block:
            parts.append(block['text']['text'])
        for element in block.get('elements', []):
            if 'text' in element:
                text = element['text']
                parts.append(text['text'] if isinstance(text, dict) else text)
    return '\n'.join(parts)


def _block(payload, block_type):
    return [b for b in payload['blocks'] if b['type'] == block_type]


class StaffCommentAlertBase(TestCase):
    """Shared fixture: lesson, stepper homework, workshop, member threads."""

    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(
            email='main@test.com', password='pw',
            first_name='Mia', last_name='Main',
        )
        cls.other_member = User.objects.create_user(
            email='free@test.com', password='pw', first_name='Fred',
        )
        cls.staff = User.objects.create_user(
            email='admin@test.com', password='pw', is_staff=True,
        )

        cls.course = Course.objects.create(
            title='LLM Zoomcamp', slug='llm-zoomcamp', status='published',
        )
        cls.module = Module.objects.create(
            course=cls.course, title='Module 1', slug='module-1', sort_order=1,
        )
        cls.lesson = Unit.objects.create(
            module=cls.module, title='Intro to RAG', slug='intro-to-rag',
            sort_order=1, kind='lesson', content_id=uuid.uuid4(),
        )
        cls.hw_unit = Unit.objects.create(
            module=cls.module, title='Homework One', slug='hw1',
            sort_order=2, kind='homework', content_id=uuid.uuid4(),
        )
        cohort = Cohort.objects.create(
            course=cls.course, name='Cohort 1',
            start_date=timezone.now().date() - timedelta(days=1),
        )
        homework = Homework.objects.create(
            cohort=cohort, slug='hw1', title='Homework One',
            content_id=cls.hw_unit.content_id, stepper_enabled=True,
        )
        for source_question_id in ('q1-first', 'q2-units'):
            Question.objects.create(
                homework=homework, source_question_id=source_question_id,
                text='Q', question_type=QuestionType.FREE_FORM,
            )
        cls.q2_thread = HomeworkStepThread.objects.create(
            unit_content_id=cls.hw_unit.content_id,
            step_slug='q2-units',
            content_id=step_thread_content_id(cls.hw_unit.content_id, 'q2-units'),
        )

        cls.workshop = Workshop.objects.create(
            title='Agents 101', slug='agents-101',
            date=date(2026, 4, 21), status='published',
        )
        cls.page = WorkshopPage.objects.create(
            workshop=cls.workshop, slug='setup', title='Setup',
            sort_order=1, body='Body', content_id=uuid.uuid4(),
        )

    def setUp(self):
        self._set('SLACK_ENABLED', 'true')
        self._set('SLACK_BOT_TOKEN', 'xoxb-test')
        self._set('SITE_BASE_URL', BASE_URL)
        self._set('STAFF_COMMENT_NOTIFY_CHANNEL_ID', 'C_COMMENTS')
        self.addCleanup(clear_config_cache)
        patcher = mock.patch(POST_TARGET, return_value=_ok_response())
        self.post = patcher.start()
        self.addCleanup(patcher.stop)

    def _set(self, key, value):
        IntegrationSetting.objects.update_or_create(
            key=key, defaults={'value': value},
        )
        clear_config_cache()

    def _unset(self, key):
        IntegrationSetting.objects.filter(key=key).delete()
        clear_config_cache()

    def _comment(self, content_id, user=None, parent=None, body='A question'):
        return create_comment(
            content_id=content_id,
            user=user or self.member,
            body=body,
            parent=parent,
        )


@tag('core')
class StaffCommentAlertContentTest(StaffCommentAlertBase):
    """Message shape and deep links for each alerting surface."""

    def test_lesson_comment_posts_one_message_with_author_titles_and_link(self):
        self._comment(self.lesson.content_id, body='How do I chunk PDFs?')

        self.assertEqual(self.post.call_count, 1)
        call = self.post.call_args
        self.assertEqual(call.args[0], 'https://slack.com/api/chat.postMessage')
        self.assertEqual(
            call.kwargs['headers']['Authorization'], 'Bearer xoxb-test',
        )
        self.assertLessEqual(sum(call.kwargs['timeout']), 8.05)

        payload = _payload(self.post)
        self.assertEqual(payload['channel'], 'C_COMMENTS')
        self.assertEqual(payload['text'], 'New comment on Intro to RAG')
        lesson_url = (
            f'{BASE_URL}{self.lesson.get_absolute_url()}#qa-section'
        )
        headline = payload['blocks'][0]['text']['text']
        self.assertEqual(
            headline,
            f'<{BASE_URL}/studio/users/{self.member.pk}/|Mia Main> '
            f'(main@test.com) commented on '
            f'<{lesson_url}|LLM Zoomcamp: Intro to RAG>',
        )
        self.assertEqual(
            _block(payload, 'context')[0]['elements'][0]['text'],
            'Course lesson',
        )
        self.assertIn('>How do I chunk PDFs?', _blocks_text(payload))
        button = _block(payload, 'actions')[0]['elements'][0]
        self.assertEqual(button['text']['text'], 'Open discussion')
        self.assertEqual(button['url'], lesson_url)

    def test_homework_step_comment_links_to_that_step_page(self):
        self._comment(
            self.q2_thread.content_id, body='Is the answer in MB or GB?',
        )

        self.assertEqual(self.post.call_count, 1)
        payload = _payload(self.post)
        step_url = (
            f'{BASE_URL}{self.hw_unit.get_absolute_url()}/q2-units#qa-section'
        )
        button = _block(payload, 'actions')[0]['elements'][0]
        self.assertEqual(button['url'], step_url)
        self.assertEqual(
            payload['text'], 'New comment on Homework One — Question 2',
        )
        self.assertIn(
            f'<{step_url}|LLM Zoomcamp: Homework One — Question 2>',
            payload['blocks'][0]['text']['text'],
        )
        self.assertEqual(
            _block(payload, 'context')[0]['elements'][0]['text'],
            'Homework step',
        )

    def test_workshop_page_comment_names_workshop_and_links_page(self):
        self._comment(self.page.content_id)

        payload = _payload(self.post)
        page_url = f'{BASE_URL}{self.page.get_absolute_url()}#qa-section'
        self.assertEqual(
            _block(payload, 'actions')[0]['elements'][0]['url'], page_url,
        )
        self.assertIn(
            f'<{page_url}|Agents 101: Setup>',
            payload['blocks'][0]['text']['text'],
        )
        self.assertEqual(
            _block(payload, 'context')[0]['elements'][0]['text'],
            'Workshop page',
        )

    def test_reply_uses_reply_fallback_and_names_parent_author(self):
        top = self._comment(
            self.page.content_id, user=self.other_member, body='uv fails',
        )
        self.post.reset_mock()

        self._comment(
            self.page.content_id, parent=top, body='Try upgrading uv',
        )

        self.assertEqual(self.post.call_count, 1)
        payload = _payload(self.post)
        self.assertEqual(payload['text'], 'New reply on Setup')
        self.assertIn(
            '(main@test.com) replied to Fred on <',
            payload['blocks'][0]['text']['text'],
        )

    def test_thread_link_comes_from_content_comment_urls(self):
        with mock.patch(
            'notifications.services.staff_comment_alerts.content_comment_urls',
            return_value=('/custom/thread#qa-section',),
        ):
            self._comment(self.lesson.content_id)

        payload = _payload(self.post)
        self.assertEqual(
            _block(payload, 'actions')[0]['elements'][0]['url'],
            f'{BASE_URL}/custom/thread#qa-section',
        )

    def test_long_multiline_preview_is_collapsed_cut_and_escaped(self):
        body = '<!channel> please help\n\n' + ('line of text\n' * 60)
        self._comment(self.lesson.content_id, body=body)

        payload = _payload(self.post)
        preview_block = payload['blocks'][2]['text']['text']
        self.assertTrue(preview_block.startswith('>'))
        preview = preview_block[1:]
        self.assertNotIn('\n', preview)
        self.assertTrue(preview.endswith('…'))
        raw = preview.replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&')
        self.assertEqual(len(raw), 301)
        self.assertTrue(preview.startswith('&lt;!channel&gt; please help line'))
        self.assertNotIn('<!channel>', json.dumps(payload))

    def test_every_text_object_is_verbatim_and_mentions_are_not_parsed(self):
        """Plain @channel/@here/@everyone in member text never pings."""
        loud = User.objects.create_user(
            email='loud@test.com', password='pw',
            first_name='@here', last_name='@everyone',
        )
        top = self._comment(self.lesson.content_id, user=loud, body='first')
        self.post.reset_mock()

        self._comment(
            self.lesson.content_id, parent=top,
            body='@channel please help @here @everyone #general',
        )

        payload = _payload(self.post)
        text_objects = [
            block['text'] for block in payload['blocks'] if 'text' in block
        ] + [
            element for block in payload['blocks']
            for element in block.get('elements', [])
            if element.get('type') == 'mrkdwn'
        ]
        # Headline, context line, and preview.
        self.assertEqual(len(text_objects), 3)
        for text_object in text_objects:
            self.assertEqual(text_object['type'], 'mrkdwn')
            self.assertIs(text_object['verbatim'], True)
        self.assertIs(payload['link_names'], False)
        self.assertEqual(payload['parse'], 'none')
        self.assertIn(
            'replied to @here @everyone on', text_objects[0]['text'],
        )
        self.assertEqual(
            payload['blocks'][2]['text']['text'],
            '>@channel please help @here @everyone #general',
        )

    def test_user_supplied_names_and_titles_are_escaped(self):
        sneaky = User.objects.create_user(
            email='sneaky@test.com', password='pw',
            first_name='<https://evil.test|Boss>', last_name='&Co',
        )
        self.page.title = 'Setup <!here>'
        self.page.save(update_fields=['title'])

        self._comment(self.page.content_id, user=sneaky)

        payload = _payload(self.post)
        headline = payload['blocks'][0]['text']['text']
        self.assertIn('|&lt;https://evil.test|Boss&gt; &amp;Co>', headline)
        self.assertIn('Setup &lt;!here&gt;', headline)
        self.assertEqual(payload['text'], 'New comment on Setup &lt;!here&gt;')
        self.assertNotIn('<!here>', json.dumps(payload))


@tag('core')
class StaffCommentAlertGatingTest(StaffCommentAlertBase):
    """Who and what never alerts, and configuration gates."""

    def test_staff_author_never_alerts(self):
        self._comment(self.lesson.content_id, user=self.staff)
        top = self._comment(self.lesson.content_id)
        # Only the member's comment posted.
        self.assertEqual(self.post.call_count, 1)
        self.post.reset_mock()

        self._comment(self.lesson.content_id, user=self.staff, parent=top)

        self.post.assert_not_called()

    def test_operator_api_reply_with_staff_token_does_not_alert(self):
        _token, plaintext = Token.create_for_user(user=self.staff, name='ops')
        top = self._comment(self.lesson.content_id)
        self.post.reset_mock()

        response = self.client.post(
            f'/api/comments/{top.pk}/replies',
            data=json.dumps({'body': 'Use a page-aware splitter'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {plaintext}',
            HTTP_IDEMPOTENCY_KEY='staff-reply-1926',
        )

        self.assertEqual(response.status_code, 201)
        self.post.assert_not_called()

    def test_book_club_note_and_plan_threads_do_not_alert(self):
        book = Book.objects.create(
            title='Inference Engineering', slug='inference-engineering',
            author='Philip Kiely', required_level=0, status='current',
            start_date=date(2026, 8, 10),
        )
        chapter = Chapter.objects.create(book=book, number=1, title='Serving')
        note = Note.objects.create(
            chapter=chapter, user=self.member, body='KV-cache notes',
        )
        sprint = Sprint.objects.create(
            name='August Sprint', slug='august-sprint',
            start_date=date(2026, 8, 1),
        )
        plan = Plan.objects.create(
            member=self.member, sprint=sprint, title='Ship it',
        )

        self._comment(note.comment_content_id, user=self.other_member)
        self._comment(plan.comment_content_id)
        self._comment(uuid.uuid4())

        self.post.assert_not_called()
        # The member-owned note still notifies its owner in-app.
        self.assertTrue(
            Notification.objects.filter(
                user=self.member, notification_type='content_comment',
            ).exists(),
        )

    def test_blank_comment_channel_falls_back_to_signup_channel(self):
        self._unset('STAFF_COMMENT_NOTIFY_CHANNEL_ID')
        self._set('STAFF_SIGNUP_NOTIFY_CHANNEL_ID', 'C_STAFF')

        self._comment(self.lesson.content_id)

        self.assertEqual(_payload(self.post)['channel'], 'C_STAFF')

    def test_both_channels_blank_makes_no_call_and_api_succeeds(self):
        self._unset('STAFF_COMMENT_NOTIFY_CHANNEL_ID')
        self._unset('STAFF_SIGNUP_NOTIFY_CHANNEL_ID')
        self.client.force_login(self.member)

        with self.assertLogs(
            'notifications.services.staff_comment_alerts', level='INFO',
        ):
            response = self.client.post(
                f'/api/comments/{self.lesson.content_id}',
                data=json.dumps({'body': 'Anyone there?'}),
                content_type='application/json',
            )

        self.assertEqual(response.status_code, 201)
        self.post.assert_not_called()

    def test_disabled_switches_make_no_call(self):
        for key, value in (
            ('STAFF_COMMENT_NOTIFY_ENABLED', 'false'),
            ('SLACK_ENABLED', 'false'),
            ('SLACK_BOT_TOKEN', ''),
        ):
            with self.subTest(key=key):
                original = IntegrationSetting.objects.filter(key=key).first()
                self._set(key, value)
                self._comment(self.lesson.content_id)
                self.post.assert_not_called()
                if original is None:
                    self._unset(key)
                else:
                    self._set(key, original.value)
        # Restoring every switch posts again.
        self._comment(self.lesson.content_id)
        self.assertEqual(self.post.call_count, 1)

    def test_kill_switch_defaults_on(self):
        self._unset('STAFF_COMMENT_NOTIFY_ENABLED')

        self._comment(self.lesson.content_id)

        self.assertEqual(self.post.call_count, 1)

    def test_moderation_and_votes_never_alert(self):
        comment = self._comment(self.lesson.content_id)
        self.post.reset_mock()

        edit_comment_body(comment, body='edited')
        hide_comment(comment)
        restore_comment(comment)
        self.client.force_login(self.other_member)
        response = self.client.post(f'/api/comments/{comment.pk}/vote')

        self.assertEqual(response.json(), {'voted': True, 'vote_count': 1})
        self.post.assert_not_called()


@tag('core')
class StaffCommentAlertFailureTest(StaffCommentAlertBase):
    """Slack failures are logged with the comment id and swallowed."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.instructor_user = User.objects.create_user(
            email='instructor@test.com', password='pw', first_name='Ada',
        )
        instructor = Instructor.objects.create(
            instructor_id='ada', name='Ada', status='published',
            user=cls.instructor_user,
        )
        cls.course.instructors.add(instructor)

    def _assert_isolated(self, post_side_effect=None, post_return=None):
        self.post.side_effect = post_side_effect
        if post_return is not None:
            self.post.return_value = post_return
        self.client.force_login(self.member)

        with self.assertLogs(
            'notifications.services.staff_comment_alerts', level='ERROR',
        ) as logs:
            response = self.client.post(
                f'/api/comments/{self.lesson.content_id}',
                data=json.dumps({'body': 'Video does not load'}),
                content_type='application/json',
            )

        self.assertEqual(response.status_code, 201)
        comment = Comment.objects.get(body='Video does not load')
        self.assertEqual(response.json()['id'], comment.pk)
        self.assertIn(str(comment.pk), '\n'.join(logs.output))
        self.assertTrue(
            Notification.objects.filter(
                user=self.instructor_user,
                notification_type='content_comment',
            ).exists(),
        )
        self.assertTrue(
            EmailDelivery.objects.filter(
                purpose='content_comment',
                recipient_email='instructor@test.com',
            ).exists(),
        )

    def test_timeout_is_swallowed(self):
        self._assert_isolated(post_side_effect=requests.exceptions.Timeout())

    def test_unexpected_exception_is_swallowed(self):
        self._assert_isolated(post_side_effect=RuntimeError('boom'))

    def test_not_ok_response_is_logged(self):
        response = mock.Mock()
        response.json.return_value = {'ok': False, 'error': 'not_in_channel'}
        self._assert_isolated(post_return=response)
