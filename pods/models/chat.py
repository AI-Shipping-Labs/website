"""Pod chat messages (issue #1920).

A small plain-text chat on the pod page. Staff read it and moderate it with
the same soft-hide pattern as ``comments.Comment`` (#1894): a set
``hidden_at`` hides the message from members while Studio and the staff API
keep it. The posting and moderation rules live in ``pods.services.chat`` so
the member page, Studio and the staff API share them.
"""

from django.conf import settings
from django.db import models

from pods.models.pod import Pod

__all__ = ['POD_MESSAGE_MAX_LENGTH', 'PodMessage']

POD_MESSAGE_MAX_LENGTH = 1000


class PodMessage(models.Model):
    """One plain-text chat message in a pod."""

    pod = models.ForeignKey(Pod, on_delete=models.CASCADE, related_name='messages')
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='pod_messages',
    )
    body = models.TextField(max_length=POD_MESSAGE_MAX_LENGTH)
    created_at = models.DateTimeField(auto_now_add=True)
    hidden_at = models.DateTimeField(null=True, blank=True, db_index=True)
    hidden_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
    )

    class Meta:
        ordering = ['created_at', 'pk']
        indexes = [
            models.Index(fields=['pod', 'created_at'], name='pod_message_pod_created_idx'),
        ]

    def __str__(self):
        return f'{self.author} in {self.pod} at {self.created_at:%Y-%m-%d %H:%M}'

    @property
    def is_hidden(self):
        return self.hidden_at is not None
