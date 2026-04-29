from django.conf import settings
from django.db import models

from accounts.models.user import IMPORT_BATCH_SOURCE_CHOICES


class ImportBatch(models.Model):
    """Audit trail for one external user-import operation."""

    STATUS_RUNNING = "running"
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"

    STATUS_CHOICES = [
        (STATUS_RUNNING, "Running"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_FAILED, "Failed"),
    ]

    source = models.CharField(
        max_length=32,
        choices=IMPORT_BATCH_SOURCE_CHOICES,
        db_index=True,
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="import_batches",
        null=True,
        blank=True,
    )
    started_at = models.DateTimeField(auto_now_add=True, db_index=True)
    finished_at = models.DateTimeField(null=True, blank=True, db_index=True)
    dry_run = models.BooleanField(default=False, db_index=True)
    status = models.CharField(
        max_length=16,
        choices=STATUS_CHOICES,
        default=STATUS_RUNNING,
        db_index=True,
    )
    users_created = models.PositiveIntegerField(default=0)
    users_updated = models.PositiveIntegerField(default=0)
    users_skipped = models.PositiveIntegerField(default=0)
    emails_queued = models.PositiveIntegerField(default=0)
    errors = models.JSONField(default=list, blank=True)
    summary = models.TextField(blank=True, default="")
    params = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return f"ImportBatch({self.source}, {self.status}, {self.started_at:%Y-%m-%d %H:%M})"
