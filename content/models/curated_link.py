from django.db import models


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
    source = models.CharField(max_length=200, blank=True, default='')
    sort_order = models.IntegerField(default=0)
    published = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['sort_order', 'title']

    def __str__(self):
        return self.title

    @property
    def category_label(self):
        return self.CATEGORY_LABELS.get(self.category, self.category)

    @property
    def is_external(self):
        return self.url.startswith('http')

    @property
    def category_icon_name(self):
        icons = {
            'tools': 'wrench',
            'models': 'cpu',
            'courses': 'graduation-cap',
            'other': 'folder-open',
        }
        return icons.get(self.category, 'folder-open')
