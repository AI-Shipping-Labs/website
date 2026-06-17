import uuid
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from accounts.utils.activation import mark_activated
from comments.models import Comment
from comments.services import create_comment

User = get_user_model()


@tag('core')
class CreateCommentServiceTest(TestCase):
    def test_creates_top_level_comment_and_activates_user(self):
        user = User.objects.create_user(
            email='service-comment@test.com',
            password='pass',
            account_activated=False,
        )
        content_id = uuid.uuid4()

        with mock.patch(
            'comments.services.mark_activated',
            wraps=mark_activated,
        ) as mocked_mark_activated:
            comment = create_comment(
                content_id=content_id,
                user=user,
                body='Service comment',
            )

        self.assertIsNone(comment.parent)
        self.assertEqual(comment.content_id, content_id)
        self.assertEqual(comment.user, user)
        self.assertEqual(comment.body, 'Service comment')
        mocked_mark_activated.assert_called_once_with(user)
        user.refresh_from_db()
        self.assertTrue(user.account_activated)

    def test_creates_reply_and_activates_user(self):
        author = User.objects.create_user(
            email='service-parent@test.com',
            password='pass',
        )
        user = User.objects.create_user(
            email='service-reply@test.com',
            password='pass',
            account_activated=False,
        )
        parent = Comment.objects.create(
            content_id=uuid.uuid4(),
            user=author,
            body='Parent',
        )

        with mock.patch(
            'comments.services.mark_activated',
            wraps=mark_activated,
        ) as mocked_mark_activated:
            reply = create_comment(
                content_id=parent.content_id,
                user=user,
                parent=parent,
                body='Service reply',
            )

        self.assertEqual(reply.parent, parent)
        self.assertEqual(reply.content_id, parent.content_id)
        mocked_mark_activated.assert_called_once_with(user)
        user.refresh_from_db()
        self.assertTrue(user.account_activated)

    def test_already_activated_user_remains_activated(self):
        user = User.objects.create_user(
            email='service-active@test.com',
            password='pass',
            account_activated=True,
        )

        comment = create_comment(
            content_id=uuid.uuid4(),
            user=user,
            body='Already active',
        )

        self.assertIsNotNone(comment.pk)
        user.refresh_from_db()
        self.assertTrue(user.account_activated)
