import uuid

from django.db import models


CONTENT_TYPE_CHOICES = [
    ('article', 'Article'),
    ('course', 'Course'),
    ('resource', 'Resource'),
    ('project', 'Project'),
]

SYNC_STATUS_CHOICES = [
    ('success', 'Success'),
    ('partial', 'Partial'),
    ('failed', 'Failed'),
    ('running', 'Running'),
]


class ContentSource(models.Model):
    """A GitHub repository configured as a content source for the platform."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    repo_name = models.CharField(
        max_length=300, unique=True,
        help_text="Full GitHub repo name (e.g. AI-Shipping-Labs/blog).",
    )
    content_type = models.CharField(
        max_length=20, choices=CONTENT_TYPE_CHOICES,
        help_text="Type of content this repo contains.",
    )
    webhook_secret = models.CharField(
        max_length=200, blank=True, default='',
        help_text="Secret for validating GitHub webhook signatures.",
    )
    is_private = models.BooleanField(
        default=False,
        help_text="Whether the repo is private (requires GitHub App auth).",
    )
    last_synced_at = models.DateTimeField(
        null=True, blank=True,
        help_text="When the last sync completed.",
    )
    last_sync_status = models.CharField(
        max_length=20, blank=True, null=True, default=None,
        choices=SYNC_STATUS_CHOICES,
        help_text="Status of the last sync.",
    )
    last_sync_log = models.TextField(
        blank=True, null=True, default=None,
        help_text="Log output from the last sync.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['repo_name']

    def __str__(self):
        return f'{self.repo_name} ({self.content_type})'

    @property
    def short_name(self):
        """Return just the repo name without the org prefix."""
        return self.repo_name.split('/')[-1] if '/' in self.repo_name else self.repo_name


class SyncLog(models.Model):
    """Log entry for a content sync operation."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source = models.ForeignKey(
        ContentSource, on_delete=models.CASCADE, related_name='sync_logs',
    )
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=20, choices=SYNC_STATUS_CHOICES, default='running',
    )
    items_created = models.IntegerField(default=0)
    items_updated = models.IntegerField(default=0)
    items_deleted = models.IntegerField(default=0)
    errors = models.JSONField(
        default=list, blank=True,
        help_text="List of error objects: [{file, error}, ...]",
    )

    class Meta:
        ordering = ['-started_at']

    def __str__(self):
        return f'{self.source.repo_name} - {self.status} at {self.started_at}'

    @property
    def total_items(self):
        return self.items_created + self.items_updated + self.items_deleted

    @property
    def duration_seconds(self):
        if self.finished_at and self.started_at:
            return (self.finished_at - self.started_at).total_seconds()
        return None
