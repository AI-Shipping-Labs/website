"""IntegrationSetting model for storing integration credentials in the database."""

from django.db import models


class IntegrationSetting(models.Model):
    """Stores integration configuration key-value pairs.

    Allows admins to configure integration credentials (Stripe, Zoom, SES, etc.)
    from the Studio UI instead of requiring env vars or server access.
    """

    key = models.CharField(max_length=255, unique=True)
    value = models.TextField(blank=True)
    is_secret = models.BooleanField(default=False)
    group = models.CharField(max_length=50, db_index=True)
    description = models.CharField(max_length=255, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['group', 'key']

    def __str__(self):
        return f'{self.group}/{self.key}'
