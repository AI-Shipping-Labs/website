from django.conf import settings
from django.db import models


class EventRegistration(models.Model):
    """Registration of a user for an event."""

    event = models.ForeignKey(
        'events.Event',
        on_delete=models.CASCADE,
        related_name='registrations',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='event_registrations',
    )
    registered_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [('event', 'user')]
        ordering = ['-registered_at']

    def __str__(self):
        return f'{self.user} - {self.event}'
