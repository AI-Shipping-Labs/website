from django.db import models


class WebhookLog(models.Model):
    """Log of incoming webhooks from external services."""
    service = models.CharField(max_length=100)
    event_type = models.CharField(max_length=200, blank=True, default='')
    payload = models.JSONField(default=dict, blank=True)
    received_at = models.DateTimeField(auto_now_add=True)
    processed = models.BooleanField(default=False)

    class Meta:
        ordering = ['-received_at']

    def __str__(self):
        return f'{self.service} - {self.event_type} at {self.received_at}'
