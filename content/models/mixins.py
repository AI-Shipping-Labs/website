from django.db import models


class SourceMetadataMixin(models.Model):
    """GitHub source fields for synced rows.

    Use this mixin when a model is synced from a content repository and
    Studio/admin/sync code should read ``source_repo``, ``source_path``, and
    ``source_commit`` directly from the model. Pair it with
    ``SyncedContentIdentityMixin`` only when rows are keyed by a stable
    frontmatter UUID; models such as modules and curated links have their own
    identity contracts.
    """

    source_repo = models.CharField(
        max_length=300, blank=True, null=True, default=None,
        help_text="GitHub repo this content was synced from.",
    )
    source_path = models.CharField(
        max_length=500, blank=True, null=True, default=None,
        help_text="File path within the source repo.",
    )
    source_commit = models.CharField(
        max_length=40, blank=True, null=True, default=None,
        help_text="Git commit SHA of the last sync.",
    )

    class Meta:
        abstract = True


class SyncedContentIdentityMixin(models.Model):
    """Stable frontmatter UUID for synced content rows."""

    content_id = models.UUIDField(
        unique=True, null=True, blank=True,
        help_text="Stable UUID from frontmatter for linking user-generated data.",
    )

    class Meta:
        abstract = True


class TimestampedModelMixin(models.Model):
    """Standard created/updated timestamps for content models."""

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
