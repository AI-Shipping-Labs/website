from django.db import models

from content.access import VISIBILITY_CHOICES, get_required_tier_name
from content.models.mixins import SourceMetadataMixin, TimestampedModelMixin


class CuratedLink(SourceMetadataMixin, TimestampedModelMixin, models.Model):
    """Curated link in the collection."""
    CATEGORY_CHOICES = [
        ('workshops', 'Workshops'),
        ('courses', 'Courses'),
        ('articles', 'Articles'),
        ('tools', 'Tools'),
        ('models', 'Models'),
        ('other', 'Other'),
    ]

    CATEGORY_LABELS = {
        'workshops': 'Workshops',
        'courses': 'Courses',
        'articles': 'Articles',
        'tools': 'Tools',
        'models': 'Models',
        'other': 'Other',
    }

    CATEGORY_DESCRIPTIONS = {
        'workshops': 'Hands-on workshop materials and tutorials',
        'courses': 'Courses and learning tracks',
        'articles': 'Long-form posts and writeups',
        'tools': 'GitHub repos, CLIs, and dev tools',
        'models': 'Model hubs, runtimes, and inference',
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
            'workshops': 'graduation-cap',
            'courses': 'book-open',
            'articles': 'file-text',
            'tools': 'wrench',
            'models': 'cpu',
            'other': 'folder-open',
        }
        return icons.get(self.category, 'folder-open')
