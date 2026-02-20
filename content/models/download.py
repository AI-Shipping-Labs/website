from django.db import models

from content.access import VISIBILITY_CHOICES


FILE_TYPE_CHOICES = [
    ('pdf', 'PDF'),
    ('zip', 'ZIP'),
    ('slides', 'Slides'),
    ('notebook', 'Notebook'),
    ('csv', 'CSV'),
    ('other', 'Other'),
]


class Download(models.Model):
    """Downloadable resource (PDF, slides, zip, etc.)."""
    title = models.CharField(max_length=300)
    slug = models.SlugField(max_length=300, unique=True)
    description = models.TextField(blank=True, default='')
    file_url = models.URLField(
        max_length=500,
        help_text="URL to the downloadable file (S3, storage, etc.).",
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
    required_level = models.IntegerField(
        default=0,
        choices=VISIBILITY_CHOICES,
        help_text="Minimum tier level required to download. 0 = lead magnet (email signup).",
    )
    tags = models.JSONField(default=list, blank=True)
    download_count = models.PositiveIntegerField(default=0)
    published = models.BooleanField(default=True)
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
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

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
