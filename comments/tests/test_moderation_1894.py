"""Staff moderation of public Q&A threads (issue #1894).

Covers the browser-facing side: the ``can_moderate`` flag and hidden-row
filtering on ``GET /api/comments/<content_id>``, and the staff-only
session semantics of ``POST /api/comments/<comment_id>/edit`` and
``/hide``. Operator-token behaviour lives in
``api/tests/test_comment_moderation_1894.py``.
"""

import json
import uuid
from http import HTTPStatus

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from comments import services as comment_services
from comments.models import Comment, CommentVote
from notifications.models import Notification

User = get_user_model()


class ModerationListPayloadTest(TestCase):
    success_status = HTTPStatus.OK
    """GET /api/comments/<content_id> filtering and flags (issue #1894)."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff-1894@test.com', password='pw', is_staff=True,
        )
        cls.author = User.objects.create_user(
            email='author-1894@test.com', password='pw',
        )
        cls.viewer = User.objects.create_user(
            email='viewer-1894@test.com', password='pw',
        )
        cls.content_id = uuid.uuid4()
        cls.top = Comment.objects.create(
            content_id=cls.content_id, user=cls.author, body='Top question',
        )
        cls.reply = Comment.objects.create(
            content_id=cls.content_id, user=cls.viewer,
            parent=cls.top, body='Reply body',
        )
        CommentVote.objects.create(comment=cls.top, user=cls.viewer)

    def _visible_bodies(self):
        response = self.client.get(f'/api/comments/{self.content_id}')
        self.assertEqual(response.status_code, self.success_status)
        data = response.json()
        bodies = [comment['body'] for comment in data['comments']]
        for comment in data['comments']:
            bodies.extend(reply['body'] for reply in comment['replies'])
        return data, bodies

    def test_hidden_comment_omitted_for_every_viewer_including_staff(self):
        comment_services.hide_comment(self.top)

        for email in (None, 'viewer-1894@test.com', 'staff-1894@test.com'):
            if email:
                self.client.force_login(User.objects.get(email=email))
            else:
                self.client.logout()
            data, bodies = self._visible_bodies()
            self.assertEqual(data['comments'], [])
            self.assertNotIn('Top question', bodies)
            # The reply row itself was not independently hidden; it only
            # leaves the thread because its parent is hidden.
            self.reply.refresh_from_db()
            self.assertIsNone(self.reply.hidden_at)
            self.top.refresh_from_db()
            self.assertIsNotNone(self.top.hidden_at)

    def test_hidden_reply_omitted_while_parent_stays_visible(self):
        comment_services.hide_comment(self.reply)

        for email in (None, 'staff-1894@test.com'):
            if email:
                self.client.force_login(User.objects.get(email=email))
            else:
                self.client.logout()
            data, bodies = self._visible_bodies()
            self.assertEqual(len(data['comments']), 1)
            self.assertIn('Top question', bodies)
            self.assertNotIn('Reply body', bodies)
            self.assertEqual(data['comments'][0]['replies'], [])

    def test_restore_via_service_brings_comment_back(self):
        comment_services.hide_comment(self.top)
        comment_services.restore_comment(self.top)
        _data, bodies = self._visible_bodies()
        self.assertIn('Top question', bodies)
        self.assertIn('Reply body', bodies)

    def test_votes_and_rows_survive_hide(self):
        comment_services.hide_comment(self.top)
        self.assertTrue(Comment.objects.filter(pk=self.top.pk).exists())
        self.assertTrue(
            CommentVote.objects.filter(comment=self.top, user=self.viewer).exists(),
        )

    def test_is_edited_flag_reflects_service_edit_only(self):
        self.client.force_login(self.staff)
        data, _bodies = self._visible_bodies()
        self.assertFalse(data['comments'][0]['is_edited'])

        comment_services.edit_comment_body(self.top, body='Rewritten body')
        data, bodies = self._visible_bodies()
        self.assertTrue(data['comments'][0]['is_edited'])
        self.assertIn('Rewritten body', bodies)

        # Hide/restore must not add the marker.
        comment_services.hide_comment(self.top)
        comment_services.restore_comment(self.top)
        data, _bodies = self._visible_bodies()
        self.assertTrue(data['comments'][0]['is_edited'])

    def test_can_moderate_flag_matches_staff_viewer(self):
        response = self.client.get(f'/api/comments/{self.content_id}')
        self.assertFalse(response.json()['can_moderate'])

        self.client.force_login(self.viewer)
        response = self.client.get(f'/api/comments/{self.content_id}')
        self.assertFalse(response.json()['can_moderate'])

        self.client.force_login(self.staff)
        response = self.client.get(f'/api/comments/{self.content_id}')
        self.assertTrue(response.json()['can_moderate'])


class BrowserModerationPermissionTest(TestCase):
    success_status = HTTPStatus.OK
    """Staff-only matrix for the shared edit/hide routes (issue #1894)."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='mod-staff@test.com', password='pw', is_staff=True,
        )
        cls.author = User.objects.create_user(
            email='mod-author@test.com', password='pw',
        )
        cls.other = User.objects.create_user(
            email='mod-other@test.com', password='pw',
        )
        cls.content_id = uuid.uuid4()
        cls.comment = Comment.objects.create(
            content_id=cls.content_id, user=cls.author, body='Spoiler body',
        )

    def _post(self, action, comment_id=None, body=None):
        payload = json.dumps({'body': body}) if body is not None else None
        return self.client.post(
            f'/api/comments/{comment_id or self.comment.pk}/{action}',
            data=payload,
            content_type='application/json',
        )

    def test_anonymous_gets_401_on_edit_and_hide(self):
        for action in ('edit', 'hide'):
            with self.subTest(action=action):
                response = self._post(action, body='x')
                self.assertEqual(response.status_code, 401)

    def test_member_session_gets_403_and_no_write(self):
        self.client.force_login(self.other)
        for action in ('edit', 'hide'):
            with self.subTest(action=action):
                response = self._post(action, body='hacked')
                self.assertEqual(response.status_code, 403)
        self.comment.refresh_from_db()
        self.assertEqual(self.comment.body, 'Spoiler body')
        self.assertIsNone(self.comment.hidden_at)
        self.assertIsNone(self.comment.edited_at)

    def test_author_cannot_edit_or_hide_own_comment(self):
        self.client.force_login(self.author)
        for action in ('edit', 'hide'):
            with self.subTest(action=action):
                response = self._post(action, body='mine now')
                self.assertEqual(response.status_code, 403)
        self.comment.refresh_from_db()
        self.assertEqual(self.comment.body, 'Spoiler body')

    def test_staff_session_edits_in_place_without_new_row(self):
        self.client.force_login(self.staff)
        count_before = Comment.objects.count()
        response = self._post('edit', body='  Safe question?  ')
        self.assertEqual(response.status_code, self.success_status)
        data = response.json()
        self.assertEqual(data['id'], self.comment.pk)
        self.assertEqual(data['body'], 'Safe question?')
        self.assertEqual(Comment.objects.count(), count_before)
        self.comment.refresh_from_db()
        self.assertEqual(self.comment.body, 'Safe question?')
        self.assertIsNotNone(self.comment.edited_at)
        self.assertIsNone(self.comment.hidden_at)
        # Authorship is untouched by a staff edit.
        self.assertEqual(self.comment.user_id, self.author.pk)

    def test_staff_blank_edit_is_rejected_and_changes_nothing(self):
        self.client.force_login(self.staff)
        response = self._post('edit', body='   ')
        self.assertEqual(response.status_code, 422)
        self.comment.refresh_from_db()
        self.assertEqual(self.comment.body, 'Spoiler body')
        self.assertIsNone(self.comment.edited_at)

    def test_edit_rejects_oversized_body(self):
        self.client.force_login(self.staff)
        response = self._post('edit', body='x' * 10_001)
        self.assertEqual(response.status_code, 422)

    def test_edit_rejects_unknown_fields(self):
        self.client.force_login(self.staff)
        response = self.client.post(
            f'/api/comments/{self.comment.pk}/edit',
            data=json.dumps({'body': 'x', 'user': 1}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 422)

    def test_missing_comment_returns_404(self):
        self.client.force_login(self.staff)
        for action in ('edit', 'hide'):
            with self.subTest(action=action):
                response = self._post(action, comment_id=99999, body='x')
                self.assertEqual(response.status_code, 404)

    def test_hidden_comment_is_404_on_public_routes_even_for_staff(self):
        comment_services.hide_comment(self.comment)
        self.client.force_login(self.staff)
        for action in ('edit', 'hide'):
            with self.subTest(action=action):
                response = self._post(action, body='x')
                self.assertEqual(response.status_code, 404)

    def test_staff_session_hides_and_public_list_updates(self):
        self.client.force_login(self.staff)
        response = self._post('hide')
        self.assertEqual(response.status_code, self.success_status)
        self.comment.refresh_from_db()
        self.assertIsNotNone(self.comment.hidden_at)

        payload = self.client.get(f'/api/comments/{self.content_id}').json()
        self.assertEqual(payload['comments'], [])

    def test_restore_is_not_reachable_through_a_browser_session(self):
        """The public thread has no restore; sessions get 401."""
        self.client.force_login(self.staff)
        response = self.client.post(
            f'/api/comments/{self.comment.pk}/restore',
        )
        self.assertEqual(response.status_code, 401)
        self.comment.refresh_from_db()
        self.assertIsNone(self.comment.hidden_at)

    def test_edit_and_hide_send_no_notifications(self):
        """Edit and hide must not add to the notifications that ordinary
        comment creation already produced (issue #1894)."""
        # A thread backed by real content so creation notifications fire:
        # a private plan notifies its member on every new comment.
        from plans.models import Plan, Sprint

        sprint = Sprint.objects.create(
            name='Moderation Sprint 1894',
            slug='moderation-sprint-1894',
            start_date=timezone.now().date(),
            status='active',
        )
        plan = Plan.objects.create(
            member=self.other, sprint=sprint, title='Moderation plan',
            visibility='private',
        )
        top = comment_services.create_comment(
            content_id=plan.comment_content_id,
            user=self.staff,
            body='Staff asks',
        )
        comment_services.create_comment(
            content_id=plan.comment_content_id,
            user=self.author,
            parent=top,
            body='Member answers',
        )
        self.assertTrue(Notification.objects.exists())
        count_before = Notification.objects.count()

        self.client.force_login(self.staff)
        self._post('edit', body='Edited body')
        self._post('hide', comment_id=top.pk)
        self.assertEqual(Notification.objects.count(), count_before)

    def test_reverse_names_resolve(self):
        self.assertEqual(
            reverse('api_comment_edit', kwargs={'comment_id': 1}),
            '/api/comments/1/edit',
        )
        self.assertEqual(
            reverse('api_comment_hide', kwargs={'comment_id': 1}),
            '/api/comments/1/hide',
        )
        self.assertEqual(
            reverse('api_comment_restore', kwargs={'comment_id': 1}),
            '/api/comments/1/restore',
        )
