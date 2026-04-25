import uuid

from django.db import models

CONTENT_TYPE_CHOICES = [
    ('article', 'Article'),
    ('course', 'Course'),
    ('resource', 'Resource'),
    ('project', 'Project'),
    ('interview_question', 'Interview Question'),
    ('event', 'Event'),
    ('workshop', 'Workshop'),
    ('instructor', 'Instructor'),
]

SYNC_STATUS_CHOICES = [
    ('success', 'Success'),
    ('partial', 'Partial'),
    ('failed', 'Failed'),
    ('queued', 'Queued'),
    ('running', 'Running'),
    ('skipped', 'Skipped'),
]


class ContentSource(models.Model):
    """A GitHub repository configured as a content source for the platform."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    repo_name = models.CharField(
        max_length=300,
        help_text="Full GitHub repo name (e.g. AI-Shipping-Labs/content).",
    )
    content_type = models.CharField(
        max_length=30, choices=CONTENT_TYPE_CHOICES,
        help_text="Type of content this repo contains.",
    )
    content_path = models.CharField(
        max_length=300, blank=True, default='',
        help_text="Subdirectory within the repo to sync from (e.g. blog/). Empty means repo root.",
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
    sync_locked_at = models.DateTimeField(
        null=True, blank=True,
        help_text="Timestamp of current sync lock. NULL when unlocked.",
    )
    sync_requested = models.BooleanField(
        default=False,
        help_text="Flag indicating a sync was requested while another was running.",
    )
    last_webhook_at = models.DateTimeField(
        null=True, blank=True,
        help_text="Timestamp of last webhook received.",
    )
    last_synced_commit = models.CharField(
        max_length=40, blank=True, default='', db_index=True,
        help_text=(
            "Git commit SHA from the last successful sync. Used to skip "
            "no-op syncs when the upstream HEAD has not changed (issue #235)."
        ),
    )
    max_files = models.PositiveIntegerField(
        default=1000,
        help_text="Safety limit on number of content files to process per sync.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['repo_name']
        unique_together = [('repo_name', 'content_type', 'content_path')]

    def __str__(self):
        return f'{self.repo_name} ({self.content_type})'

    @property
    def short_name(self):
        """Return just the repo name without the org prefix."""
        return self.repo_name.split('/')[-1] if '/' in self.repo_name else self.repo_name

    @property
    def short_synced_commit(self):
        """7-char SHA prefix for display (or empty string)."""
        return self.last_synced_commit[:7] if self.last_synced_commit else ''

    @property
    def synced_commit_url(self):
        """GitHub URL to the last-synced commit (or empty string)."""
        if not self.last_synced_commit or '/' not in self.repo_name:
            return ''
        return f'https://github.com/{self.repo_name}/commit/{self.last_synced_commit}'


class SyncLog(models.Model):
    """Log entry for a content sync operation."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source = models.ForeignKey(
        ContentSource, on_delete=models.CASCADE, related_name='sync_logs',
    )
    batch_id = models.UUIDField(
        null=True, blank=True, db_index=True,
        help_text="Shared UUID grouping SyncLogs from the same Sync All action.",
    )
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=20, choices=SYNC_STATUS_CHOICES, default='running',
    )
    items_created = models.IntegerField(default=0)
    items_updated = models.IntegerField(default=0)
    items_unchanged = models.IntegerField(
        default=0,
        help_text=(
            "Items found in the repo whose content matched the DB row exactly "
            "and were skipped (no save, no items_detail entry). See issue #225."
        ),
    )
    items_deleted = models.IntegerField(default=0)
    items_detail = models.JSONField(
        default=list, blank=True,
        help_text="List of changed items: [{title, slug, action, content_type, url}, ...]",
    )
    commit_sha = models.CharField(
        max_length=40, blank=True, default='',
        help_text=(
            "Git commit SHA this sync ran against. For ``skipped`` rows this "
            "is the SHA we compared HEAD against (issue #235)."
        ),
    )
    tiers_synced = models.BooleanField(
        default=False,
        help_text="Whether tiers.yaml was synced during this operation.",
    )
    tiers_count = models.IntegerField(
        default=0,
        help_text="Number of tiers found in tiers.yaml.",
    )
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

    @property
    def short_commit_sha(self):
        """7-char SHA prefix for display (or empty string)."""
        return self.commit_sha[:7] if self.commit_sha else ''

    @property
    def commit_url(self):
        """GitHub URL to this sync's commit (or empty string)."""
        if not self.commit_sha or not self.source_id:
            return ''
        repo_name = self.source.repo_name
        if '/' not in repo_name:
            return ''
        return f'https://github.com/{repo_name}/commit/{self.commit_sha}'
