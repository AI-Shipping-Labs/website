import uuid

from django.core.exceptions import ValidationError
from django.test import TestCase

from comments.models import Comment


class CommentCleanTest(TestCase):
    """Test the Comment model clean() validation for max depth 1."""

    @classmethod
    def setUpTestData(cls):
        from django.contrib.auth import get_user_model
        User = get_user_model()
        cls.user = User.objects.create_user(email='test@example.com', password='testpass')
        cls.content_id = uuid.uuid4()

    def test_reply_to_reply_raises_validation_error(self):
        """A reply to a reply should fail clean() validation."""
        top = Comment.objects.create(
            content_id=self.content_id, user=self.user, body='Question',
        )
        reply = Comment.objects.create(
            content_id=self.content_id, user=self.user, parent=top, body='Reply',
        )
        nested = Comment(
            content_id=self.content_id, user=self.user, parent=reply, body='Nested',
        )
        with self.assertRaises(ValidationError):
            nested.clean()

    def test_reply_to_top_level_passes_clean(self):
        """A reply to a top-level comment should pass clean()."""
        top = Comment.objects.create(
            content_id=self.content_id, user=self.user, body='Question',
        )
        reply = Comment(
            content_id=self.content_id, user=self.user, parent=top, body='Reply',
        )
        # Should not raise
        reply.clean()

    def test_str_top_level(self):
        comment = Comment.objects.create(
            content_id=self.content_id, user=self.user, body='Q',
        )
        self.assertIn('Question', str(comment))

    def test_str_reply(self):
        top = Comment.objects.create(
            content_id=self.content_id, user=self.user, body='Q',
        )
        reply = Comment.objects.create(
            content_id=self.content_id, user=self.user, parent=top, body='R',
        )
        self.assertIn('Reply', str(reply))
