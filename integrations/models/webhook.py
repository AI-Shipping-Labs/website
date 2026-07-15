from django.db import models


class WebhookLog(models.Model):
    """Log of incoming webhooks from external services."""
    service = models.CharField(max_length=100)
    event_type = models.CharField(max_length=200, blank=True, default='')
    payload = models.JSONField(default=dict, blank=True)
    received_at = models.DateTimeField(auto_now_add=True)
    processed = models.BooleanField(default=False)
    deduplication_key = models.CharField(
        max_length=128, blank=True, null=True, unique=True,
        help_text='Provider delivery fingerprint; NULL for legacy logs.',
    )
    attempts = models.PositiveIntegerField(default=0)
    error_message = models.TextField(blank=True, default='')
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-received_at']
        indexes = [
            models.Index(
                fields=['service', 'processed', 'received_at'],
                name='integration_service_7b4a40_idx',
            ),
        ]

    def __str__(self):
        return f'{self.service} - {self.event_type} at {self.received_at}'
