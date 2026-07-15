import uuid

from django.conf import settings
from django.db import models

from content.access import VISIBILITY_CHOICES
from content.models.mixins import (
    SourceMetadataMixin,
    SyncedContentIdentityMixin,
    TimestampedModelMixin,
)

FILE_TYPE_CHOICES = [
    ('pdf', 'PDF'),
    ('zip', 'ZIP'),
    ('slides', 'Slides'),
    ('notebook', 'Notebook'),
    ('csv', 'CSV'),
    ('other', 'Other'),
]

SAFE_DOWNLOAD_FILE_TYPES = {'pdf', 'zip', 'slides', 'notebook', 'csv'}
DOWNLOAD_MIME_TYPES = {
    'pdf': 'application/pdf',
    'zip': 'application/zip',
    'slides': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    'notebook': 'application/x-ipynb+json',
    'csv': 'text/csv',
}
DOWNLOAD_EXTENSION_MIME_TYPES = {
    'pdf': {'.pdf': 'application/pdf'},
    'zip': {'.zip': 'application/zip'},
    'slides': {
        '.ppt': 'application/vnd.ms-powerpoint',
        '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    },
    'notebook': {'.ipynb': 'application/x-ipynb+json'},
    'csv': {'.csv': 'text/csv'},
}


class Download(
    SyncedContentIdentityMixin,
    SourceMetadataMixin,
    TimestampedModelMixin,
    models.Model,
):
    """Downloadable resource (PDF, slides, zip, etc.)."""
    title = models.CharField(max_length=300)
    slug = models.SlugField(max_length=300, unique=True)
    description = models.TextField(blank=True, default='')
    file_url = models.URLField(
        max_length=500,
        help_text="URL to the downloadable file (S3, storage, etc.).",
    )
    storage_key = models.CharField(
        max_length=500,
        blank=True,
        default='',
        help_text=(
            'Private S3 object key. Required for secure delivery; never '
            'rendered on public surfaces.'
        ),
    )
    asset_mime_type = models.CharField(
        max_length=150,
        blank=True,
        default='',
        help_text=(
            'Validated MIME type for the private asset. Legacy `other` rows '
            'remain stored but are not deliverable unless a future policy '
            'adds an approved extension/MIME pair.'
        ),
    )
    delivery_blocked_reason = models.CharField(
        max_length=200,
        blank=True,
        default='',
        help_text=(
            'Operator-facing readiness marker set when source validation '
            'fails. Never rendered on public surfaces.'
        ),
    )
    file_type = models.CharField(
        max_length=20,
        choices=FILE_TYPE_CHOICES,
        default='pdf',
        help_text="Type of file (pdf, zip, slides, etc.).",
    )
    file_size_bytes = models.PositiveIntegerField(
        default=0,
        help_text="File size in bytes.",
    )
    cover_image_url = models.URLField(max_length=500, blank=True, default='')
    auto_banner_url = models.URLField(
        max_length=500, blank=True, default='',
        help_text=(
            "Platform-generated OG banner URL (banner-generator Lambda, "
            "issue #788). Overwritten by the auto-banner pipeline; templates "
            "should prefer ``cover_image_url`` and fall back to this."
        ),
    )
    custom_banner_url = models.URLField(
        max_length=500, blank=True, default='',
        help_text=(
            "Operator-uploaded custom banner/social image. Survives content "
            "re-sync. Wins over the generated banner; loses to a frontmatter "
            "cover_image_url."
        ),
    )
    auto_banner_title_hash = models.CharField(
        max_length=64, blank=True, default='',
        help_text=(
            "sha256 hex digest of the title used to render the current "
            "``auto_banner_url``. Used to detect title drift between syncs."
        ),
    )
    required_level = models.IntegerField(
        default=0,
        choices=VISIBILITY_CHOICES,
        help_text="Minimum tier level required to download. 0 = lead magnet (email signup).",
    )
    tags = models.JSONField(default=list, blank=True)
    download_count = models.PositiveIntegerField(default=0)
    published = models.BooleanField(default=True)
    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        from content.utils.tags import normalize_tags
        self.tags = normalize_tags(self.tags)
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return f'/downloads/{self.slug}'

    @property
    def file_type_label(self):
        """Return human-readable file type label."""
        return dict(FILE_TYPE_CHOICES).get(self.file_type, self.file_type.upper())

    @property
    def file_type_color(self):
        """Return Tailwind color classes for the file type badge."""
        colors = {
            'pdf': 'bg-red-500/20 text-red-400',
            'zip': 'bg-blue-500/20 text-blue-400',
            'slides': 'bg-purple-500/20 text-purple-400',
            'notebook': 'bg-orange-500/20 text-orange-400',
            'csv': 'bg-green-500/20 text-green-400',
            'other': 'bg-secondary text-muted-foreground',
        }
        return colors.get(self.file_type, 'bg-secondary text-muted-foreground')

    @property
    def human_file_size(self):
        """Return human-readable file size (e.g. '2.4 MB')."""
        size = self.file_size_bytes
        if size == 0:
            return ''
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size < 1024:
                if unit == 'B':
                    return f'{size} {unit}'
                return f'{size:.1f} {unit}'
            size /= 1024
        return f'{size:.1f} TB'

    def increment_download_count(self):
        """Atomically increment download_count by 1."""
        from django.db.models import F
        Download.objects.filter(pk=self.pk).update(download_count=F('download_count') + 1)
        self.refresh_from_db()

    @property
    def safe_filename(self):
        """Return a conservative attachment filename derived from the slug."""
        extension = {
            'pdf': 'pdf',
            'zip': 'zip',
            'slides': self.storage_key.rsplit('.', 1)[-1].lower(),
            'notebook': 'ipynb',
            'csv': 'csv',
        }.get(self.file_type, '')
        base = (self.slug or 'download').replace('/', '-').replace('\\', '-')
        return f'{base}.{extension}' if extension else base

    @property
    def resolved_mime_type(self):
        if self.asset_mime_type:
            return self.asset_mime_type
        extension = (
            '.' + self.storage_key.rsplit('.', 1)[-1].lower()
            if '.' in self.storage_key else ''
        )
        return DOWNLOAD_EXTENSION_MIME_TYPES.get(self.file_type, {}).get(
            extension,
            DOWNLOAD_MIME_TYPES.get(self.file_type, ''),
        )

    @property
    def delivery_ready(self):
        """Whether the row can be handed off through private storage safely."""
        if self.delivery_blocked_reason:
            return False
        try:
            from content.services.download_validation import validate_download_metadata
            validate_download_metadata(
                storage_key=self.storage_key,
                file_type=self.file_type,
                file_size_bytes=self.file_size_bytes,
                required_level=self.required_level,
                asset_mime_type=self.asset_mime_type,
            )
        except ValueError:
            return False
        return True


class DownloadDeliveryGrant(models.Model):
    """One-time, mailbox-proven authorization to request one download."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='download_delivery_grants',
    )
    download = models.ForeignKey(
        Download,
        on_delete=models.CASCADE,
        related_name='delivery_grants',
    )
    token_hash = models.CharField(max_length=64, unique=True, editable=False)
    newsletter_opt_in = models.BooleanField(default=False)
    surface = models.CharField(max_length=20, default='detail')
    expires_at = models.DateTimeField(db_index=True)
    redeemed_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(
                fields=['download', 'expires_at'],
                name='content_dlgrant_dl_exp_idx',
            ),
        ]

    def __str__(self):
        return f'{self.download_id}:{self.id}'
