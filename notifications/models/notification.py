from django.conf import settings
from django.db import models


NOTIFICATION_TYPE_CHOICES = [
    ('new_content', 'New Content'),
    ('event_reminder', 'Event Reminder'),
    ('announcement', 'Announcement'),
]


class Notification(models.Model):
    """On-platform notification for a user.

    user is nullable for broadcast notifications (e.g. Slack channel posts
    that don't target a specific user).
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='notifications',
        help_text='Target user. Null for broadcast notifications.',
    )
    title = models.CharField(max_length=300)
    body = models.TextField(blank=True, default='')
    url = models.CharField(
        max_length=500,
        blank=True,
        default='',
        help_text='Link to the content (e.g. /blog/new-article).',
    )
    notification_type = models.CharField(
        max_length=20,
        choices=NOTIFICATION_TYPE_CHOICES,
        default='new_content',
    )
    read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', '-created_at']),
            models.Index(fields=['user', 'read']),
        ]

    def __str__(self):
        return f'{self.title} ({self.notification_type})'


class EventReminderLog(models.Model):
    """Tracks which event reminders have been sent to avoid duplicates.

    Each (event, user, interval) triple should only generate one notification.
    """

    event = models.ForeignKey(
        'events.Event',
        on_delete=models.CASCADE,
        related_name='reminder_logs',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='event_reminder_logs',
    )
    interval = models.CharField(
        max_length=10,
        help_text='Reminder interval: "24h" or "1h".',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [('event', 'user', 'interval')]

    def __str__(self):
        return f'{self.user} - {self.event} ({self.interval})'
