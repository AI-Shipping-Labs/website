from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class Comment(models.Model):
    """A Q&A comment linked to content by content_id UUID."""

    content_id = models.UUIDField(
        db_index=True,
        help_text="UUID matching the content's content_id field.",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='comments',
    )
    parent = models.ForeignKey(
        'self',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='replies',
    )
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        if self.parent:
            return f'Reply by {self.user} on {self.content_id}'
        return f'Question by {self.user} on {self.content_id}'

    def clean(self):
        if self.parent and self.parent.parent is not None:
            raise ValidationError('Replies to replies are not allowed (max depth is 1).')


class CommentVote(models.Model):
    """Upvote on a top-level comment (question)."""

    comment = models.ForeignKey(
        Comment,
        on_delete=models.CASCADE,
        related_name='votes',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='comment_votes',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [('comment', 'user')]

    def __str__(self):
        return f'Vote by {self.user} on comment {self.comment_id}'
