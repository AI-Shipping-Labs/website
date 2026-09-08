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


class ApiReplyOperation(models.Model):
    """Immutable, non-secret audit/idempotency record for operator replies."""

    token_identity = models.CharField(max_length=64)
    token_name_snapshot = models.CharField(max_length=100, blank=True, default='')
    token_prefix_snapshot = models.CharField(max_length=32, blank=True, default='')
    idempotency_key = models.CharField(max_length=255)
    request_digest = models.CharField(max_length=64)
    resulting_comment = models.ForeignKey(
        Comment,
        on_delete=models.SET_NULL,
        null=True,
        related_name='api_reply_operations',
    )
    resulting_comment_id_snapshot = models.PositiveBigIntegerField()
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='+',
    )
    actor_id_snapshot = models.PositiveBigIntegerField()
    actor_email_snapshot = models.EmailField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['token_identity', 'idempotency_key'],
                name='uniq_comment_api_reply_key',
            ),
        ]
        ordering = ['-created_at', '-id']

    def save(self, *args, **kwargs):
        if self.pk is not None and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError('API reply operation records are immutable.')
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('API reply operation records are immutable.')

    def __str__(self):
        return f'Operator reply {self.resulting_comment_id_snapshot}'
