"""Operator-token comment moderation contracts for issue #1894.

Covers the staff-token side of ``POST /api/comments/{id}/edit`` (body
rewrite), ``/hide`` (soft hide), and ``/restore`` plus the
``moderation_state`` row field and filter on ``GET /api/comments``. The
browser-session semantics of the shared edit/hide routes live in
``comments/tests/test_moderation_1894.py``.
"""

import json
import uuid
from http import HTTPStatus

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import Token
from comments import services as comment_services
from comments.models import Comment
from comments.services import MODERATION_HIDDEN, MODERATION_VISIBLE
from notifications.models import Notification
from plans.models import Plan, Sprint

User = get_user_model()


class CommentModerationOperatorApiTest(TestCase):
    success_status = HTTPStatus.OK

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='mod-op-staff@test.com',
            password='pw',
            is_staff=True,
        )
        cls.member = User.objects.create_user(
            email='mod-op-member@test.com',
            password='pw',
        )
        cls.staff_token, cls.staff_plaintext = Token.create_for_user(
            user=cls.staff, name='moderation automation',
        )
        # ``Token.save`` enforces staff-only ownership, so a non-staff token
        # cannot be minted through the normal path. bulk_create seeds the row
        # this issue must still reject with 401 (a valid credential for a
        # member must authenticate to nothing).
        member_token = Token(user=cls.member, name='member token')
        member_token.set_plaintext_key(member_token.generate_plaintext_key())
        Token.objects.bulk_create([member_token])
        cls.member_plaintext = member_token.key
        cls.content_id = uuid.uuid4()
        cls.comment = Comment.objects.create(
            content_id=cls.content_id,
            user=cls.member,
            body='I had 7944 for unstructured',
        )
        cls.reply = Comment.objects.create(
            content_id=cls.content_id,
            user=cls.staff,
            parent=cls.comment,
            body='Reply leaking 186',
        )

    def auth(self, token=None):
        return {'HTTP_AUTHORIZATION': f'Token {token or self.staff_plaintext}'}

    def post_moderation(self, action, comment_id=None, token=None, body=None):
        kwargs = self.auth(token)
        if body is not None:
            kwargs['data'] = json.dumps({'body': body})
            kwargs['content_type'] = 'application/json'
        return self.client.post(
            f'/api/comments/{comment_id or self.comment.pk}/{action}',
            **kwargs,
        )

    def operator_list(self, query=''):
        return self.client.get(f'/api/comments{query}', **self.auth())

    # ---- edit -------------------------------------------------------


    def test_staff_token_edits_body_in_place(self):
        count_before = Comment.objects.count()
        response = self.client.post(
            f'/api/comments/{self.comment.pk}/edit',
            data=json.dumps({'body': '  Discuss the approach, not the numbers.  '}),
            content_type='application/json',
            **self.auth(),
        )
        self.assertEqual(response.status_code, self.success_status)
        data = response.json()
        self.assertEqual(data['id'], self.comment.pk)
        self.assertEqual(data['body'], 'Discuss the approach, not the numbers.')
        self.assertEqual(data['moderation_state'], MODERATION_VISIBLE)
        self.assertEqual(Comment.objects.count(), count_before)

        self.comment.refresh_from_db()
        self.assertEqual(self.comment.body, 'Discuss the approach, not the numbers.')
        self.assertIsNotNone(self.comment.edited_at)
        self.assertIsNone(self.comment.hidden_at)
        # Authorship and parentage are untouched.
        self.assertEqual(self.comment.user_id, self.member.pk)
        self.assertIsNone(self.comment.parent_id)

    def test_staff_token_edits_a_reply(self):
        response = self.client.post(
            f'/api/comments/{self.reply.pk}/edit',
            data=json.dumps({'body': 'Reply rewritten'}),
            content_type='application/json',
            **self.auth(),
        )
        self.assertEqual(response.status_code, self.success_status)
        self.assertEqual(response.json()['parent_id'], self.comment.pk)
        self.reply.refresh_from_db()
        self.assertEqual(self.reply.body, 'Reply rewritten')

    def test_staff_token_edits_hidden_comment_before_restore(self):
        comment_services.hide_comment(self.comment)
        response = self.client.post(
            f'/api/comments/{self.comment.pk}/edit',
            data=json.dumps({'body': 'Spoiler stripped'}),
            content_type='application/json',
            **self.auth(),
        )
        self.assertEqual(response.status_code, self.success_status)
        self.comment.refresh_from_db()
        self.assertEqual(self.comment.body, 'Spoiler stripped')
        self.assertIsNotNone(self.comment.hidden_at)

    def test_edit_rejects_blank_oversized_unknown_and_non_string(self):
        cases = [
            ('blank', {'body': '   '}),
            ('oversized', {'body': 'x' * 10_001}),
            ('unknown_field', {'body': 'x', 'user': 1}),
            ('non_string', {'body': 42}),
        ]
        for label, payload in cases:
            with self.subTest(case=label):
                response = self.client.post(
                    f'/api/comments/{self.comment.pk}/edit',
                    data=json.dumps(payload),
                    content_type='application/json',
                    **self.auth(),
                )
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.json()['code'], 'validation_error')
        self.comment.refresh_from_db()
        self.assertEqual(self.comment.body, 'I had 7944 for unstructured')
        self.assertIsNone(self.comment.edited_at)

    def test_edit_rejects_invalid_json(self):
        response = self.client.post(
            f'/api/comments/{self.comment.pk}/edit',
            data='not json',
            content_type='application/json',
            **self.auth(),
        )
        self.assertEqual(response.status_code, 400)

    # ---- hide -------------------------------------------------------


    def test_staff_token_hides_comment_and_operator_list_reports_it(self):
        response = self.post_moderation('hide')
        self.assertEqual(response.status_code, self.success_status)
        self.assertEqual(response.json()['moderation_state'], MODERATION_HIDDEN)
        self.comment.refresh_from_db()
        self.assertIsNotNone(self.comment.hidden_at)

        listing = self.operator_list(f'?content_id={self.content_id}').json()
        self.assertEqual(listing['count'], 2)
        states = {row['id']: row['moderation_state'] for row in listing['comments']}
        self.assertEqual(states[self.comment.pk], MODERATION_HIDDEN)
        self.assertEqual(states[self.reply.pk], MODERATION_VISIBLE)

    def test_hide_keeps_rows_votes_replies_intact(self):
        comment_services.hide_comment(self.comment)
        self.assertTrue(Comment.objects.filter(pk=self.comment.pk).exists())
        self.assertTrue(Comment.objects.filter(pk=self.reply.pk).exists())
        self.assertIsNone(self.reply.hidden_at)

    def test_hide_is_idempotent(self):
        first = self.post_moderation('hide')
        self.comment.refresh_from_db()
        hidden_at = self.comment.hidden_at
        second = self.post_moderation('hide')
        self.assertEqual(first.status_code, self.success_status)
        self.assertEqual(second.status_code, self.success_status)
        self.comment.refresh_from_db()
        self.assertIsNotNone(self.comment.hidden_at)
        self.assertEqual(self.comment.hidden_at, hidden_at)
        self.assertEqual(Comment.objects.count(), 2)

    def test_moderation_state_filter_on_operator_list(self):
        comment_services.hide_comment(self.comment)

        visible = self.operator_list(
            f'?content_id={self.content_id}&moderation_state=visible',
        ).json()
        self.assertEqual(
            [row['id'] for row in visible['comments']],
            [self.reply.pk],
        )

        hidden = self.operator_list(
            f'?content_id={self.content_id}&moderation_state=hidden',
        ).json()
        self.assertEqual(
            [row['id'] for row in hidden['comments']],
            [self.comment.pk],
        )

        both = self.operator_list(f'?content_id={self.content_id}').json()
        self.assertEqual(both['count'], 2)

    def test_moderation_state_filter_rejects_invalid_value(self):
        response = self.operator_list('?moderation_state=archived')
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()['code'], 'validation_error')

    def test_moderation_state_filter_empty_value_returns_nothing(self):
        response = self.operator_list('?moderation_state=')
        self.assertEqual(response.status_code, self.success_status)
        self.assertEqual(response.json()['count'], 0)

    # ---- restore ----------------------------------------------------


    def test_staff_token_restores_hidden_comment(self):
        comment_services.hide_comment(self.comment)

        response = self.post_moderation('restore')
        self.assertEqual(response.status_code, self.success_status)
        self.assertEqual(response.json()['moderation_state'], MODERATION_VISIBLE)
        self.comment.refresh_from_db()
        self.assertIsNone(self.comment.hidden_at)

        public = self.client.get(f'/api/comments/{self.content_id}').json()
        bodies = [row['body'] for row in public['comments']]
        self.assertIn('I had 7944 for unstructured', bodies)

    def test_restore_is_idempotent(self):
        first = self.post_moderation('restore')
        second = self.post_moderation('restore')
        self.assertEqual(first.status_code, self.success_status)
        self.assertEqual(second.status_code, self.success_status)
        self.comment.refresh_from_db()
        self.assertIsNone(self.comment.hidden_at)

    def test_restore_requires_a_token_not_a_session(self):
        for login_as in (None, self.staff):
            with self.subTest(session=login_as):
                self.client.logout()
                if login_as is not None:
                    self.client.force_login(login_as)
                response = self.client.post(
                    f'/api/comments/{self.comment.pk}/restore',
                )
                self.assertEqual(response.status_code, 401)
        self.comment.refresh_from_db()
        self.assertIsNone(self.comment.hidden_at)

    # ---- permission matrix ------------------------------------------


    def test_member_token_gets_401_and_no_write(self):
        for action in ('edit', 'hide', 'restore'):
            with self.subTest(action=action):
                response = self.client.post(
                    f'/api/comments/{self.comment.pk}/{action}',
                    data=json.dumps({'body': 'member rewrite'}),
                    content_type='application/json',
                    **self.auth(self.member_plaintext),
                )
                self.assertEqual(response.status_code, 401)
        self.comment.refresh_from_db()
        self.assertEqual(self.comment.body, 'I had 7944 for unstructured')
        self.assertIsNone(self.comment.hidden_at)
        self.assertIsNone(self.comment.edited_at)

    def test_missing_and_invalid_tokens_get_401(self):
        cases = [
            ('missing', {}),
            ('invalid', self.auth('not-a-real-token')),
            ('bad_scheme', {'HTTP_AUTHORIZATION': f'Bearer {self.staff_plaintext}'}),
        ]
        for label, kwargs in cases:
            with self.subTest(case=label):
                response = self.client.post(
                    f'/api/comments/{self.comment.pk}/hide', **kwargs,
                )
                self.assertEqual(response.status_code, 401)

    def test_unknown_comment_returns_404(self):
        for action in ('edit', 'hide', 'restore'):
            with self.subTest(action=action):
                response = self.post_moderation(
                    action, comment_id=999_999, body='x',
                )
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.json()['code'], 'comment_not_found')

    # ---- side effects -----------------------------------------------


    def test_moderation_writes_send_no_notifications(self):
        """Edit/hide/restore add nothing to the notifications that ordinary
        comment creation already produced (issue #1894)."""
        sprint = Sprint.objects.create(
            name='Operator Moderation Sprint',
            slug='operator-moderation-sprint',
            start_date=timezone.now().date(),
            status='active',
        )
        plan = Plan.objects.create(
            member=self.member, sprint=sprint, title='Operator plan',
            visibility='private',
        )
        top = comment_services.create_comment(
            content_id=plan.comment_content_id,
            user=self.staff,
            body='Operator asks',
        )
        self.assertTrue(Notification.objects.exists())
        count_before = Notification.objects.count()

        self.post_moderation('edit', comment_id=top.pk, body='Operator rewrites')
        self.post_moderation('hide', comment_id=top.pk)
        self.post_moderation('restore', comment_id=top.pk)
        self.assertEqual(Notification.objects.count(), count_before)
