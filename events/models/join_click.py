from django.conf import settings
from django.db import models


class EventJoinClick(models.Model):
    """Records each time a user clicks the join link for an event."""

    event = models.ForeignKey(
        'events.Event',
        on_delete=models.CASCADE,
        related_name='join_clicks',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='event_join_clicks',
    )
    clicked_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-clicked_at']

    def __str__(self):
        return f'{self.user} joined {self.event} at {self.clicked_at}'
