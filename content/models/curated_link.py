from django.db import models

from content.access import VISIBILITY_CHOICES, get_required_tier_name


class CuratedLink(models.Model):
    """Curated link in the collection."""
    CATEGORY_CHOICES = [
        ('tools', 'Tools'),
        ('models', 'Models'),
        ('courses', 'Courses'),
        ('other', 'Other'),
    ]

    CATEGORY_LABELS = {
        'tools': 'Tools',
        'models': 'Models',
        'courses': 'Courses',
        'other': 'Other',
    }

    CATEGORY_DESCRIPTIONS = {
        'tools': 'GitHub repos, CLIs, and dev tools',
        'models': 'Model hubs, runtimes, and inference',
        'courses': 'Courses and learning tracks',
        'other': 'Datasets, APIs, and more',
    }

    item_id = models.CharField(max_length=200, unique=True)
    title = models.CharField(max_length=300)
    description = models.TextField(blank=True, default='')
    url = models.URLField(max_length=500)
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES)
    tags = models.JSONField(default=list, blank=True)
    source = models.CharField(max_length=200, blank=True, default='')
    sort_order = models.IntegerField(default=0)
    required_level = models.IntegerField(
        default=0,
        choices=VISIBILITY_CHOICES,
        help_text="Minimum tier level required to view full content.",
    )
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
        ordering = ['sort_order', 'title']

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        from content.utils.tags import normalize_tags
        self.tags = normalize_tags(self.tags)
        super().save(*args, **kwargs)

    @property
    def category_label(self):
        return self.CATEGORY_LABELS.get(self.category, self.category)

    @property
    def is_external(self):
        return self.url.startswith('http')

    @property
    def required_level_tier_name(self):
        """Return the human-readable tier name for this link's required_level."""
        return get_required_tier_name(self.required_level)

    @property
    def category_icon_name(self):
        icons = {
            'tools': 'wrench',
            'models': 'cpu',
            'courses': 'graduation-cap',
            'other': 'folder-open',
        }
        return icons.get(self.category, 'folder-open')
