"""Shared-comments bridge for Book Club note threads (issue #1365, folds #1360).

Covers the tier gating the comments API applies to a ``Note.comment_content_id``
thread and the note-author notification wired through the shared comment path.
"""

import json
from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from bookclub.models import Book, Chapter, Note
from comments import services as comment_services
from comments.models import Comment
from content.access import LEVEL_MAIN
from notifications.models import Notification
from payments.models import Tier

User = get_user_model()


@tag("core")
class NoteThreadFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.book = Book.objects.create(
            title='Inference Engineering', slug='inference-engineering',
            author='Philip Kiely', required_level=LEVEL_MAIN,
            status='current', start_date=date(2026, 8, 10),
        )
        cls.chapter = Chapter.objects.create(
            book=cls.book, number=0, title='Inference',
        )
        cls.main_tier = Tier.objects.get(slug='main')
        cls.free_tier = Tier.objects.get(slug='free')

        cls.author = User.objects.create_user(email='author@test.com', password='pw')
        cls.author.tier = cls.main_tier
        cls.author.save()

        cls.reader = User.objects.create_user(email='reader@test.com', password='pw')
        cls.reader.tier = cls.main_tier
        cls.reader.save()

        cls.free_user = User.objects.create_user(email='free@test.com', password='pw')
        cls.free_user.tier = cls.free_tier
        cls.free_user.save()

        cls.note = Note.objects.create(
            chapter=cls.chapter, user=cls.author, body='The KV cache is the game.',
        )
        cls.content_id = str(cls.note.comment_content_id)


class NoteThreadGatingTest(NoteThreadFixture):
    def _api(self, method='get', **kwargs):
        url = f'/api/comments/{self.content_id}'
        return getattr(self.client, method)(url, **kwargs)

    def test_anonymous_read_returns_404(self):
        self.assertEqual(self._api('get').status_code, 404)

    def test_anonymous_write_returns_401(self):
        response = self.client.post(
            f'/api/comments/{self.content_id}',
            data=json.dumps({'body': 'hi'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 401)

    def test_below_tier_read_returns_404(self):
        self.client.force_login(self.free_user)
        self.assertEqual(self._api('get').status_code, 404)

    def test_below_tier_write_returns_403(self):
        self.client.force_login(self.free_user)
        response = self.client.post(
            f'/api/comments/{self.content_id}',
            data=json.dumps({'body': 'hi'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 403)

    def test_eligible_member_can_read_and_write(self):
        self.client.force_login(self.reader)
        self.assertEqual(self._api('get').status_code, 200)
        response = self.client.post(
            f'/api/comments/{self.content_id}',
            data=json.dumps({'body': 'Great note!'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 201)
        self.assertTrue(
            Comment.objects.filter(
                content_id=self.note.comment_content_id, user=self.reader,
            ).exists()
        )


class NoteCommentNotificationTest(NoteThreadFixture):
    def test_comment_notifies_note_author_once(self):
        comment_services.create_comment(
            content_id=self.note.comment_content_id,
            user=self.reader,
            body='Nice write-up',
        )
        author_notes = Notification.objects.filter(
            user=self.author, notification_type='content_comment',
        )
        self.assertEqual(author_notes.count(), 1)
        # Deep link points at the chapter reader page that hosts the thread.
        self.assertIn(
            '/books/inference-engineering/chapters/0',
            author_notes.first().url,
        )
        # The commenter (reader) is not notified.
        self.assertFalse(
            Notification.objects.filter(
                user=self.reader, notification_type='content_comment',
            ).exists()
        )

    def test_no_self_notify_when_author_comments_on_own_note(self):
        comment_services.create_comment(
            content_id=self.note.comment_content_id,
            user=self.author,
            body='Adding a thought',
        )
        self.assertFalse(
            Notification.objects.filter(
                user=self.author, notification_type='content_comment',
            ).exists()
        )
