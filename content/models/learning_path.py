from django.db import models


class LearningPath(models.Model):
    """A learning path synced from GitHub content repo."""

    slug = models.SlugField(max_length=300, unique=True)
    title = models.CharField(max_length=300)
    description = models.TextField(blank=True, default='')
    data_json = models.JSONField(
        default=dict, blank=True,
        help_text="Full YAML content stored as JSON.",
    )
    source_repo = models.CharField(
        max_length=300, blank=True, null=True, default=None,
    )
    source_path = models.CharField(
        max_length=500, blank=True, null=True, default=None,
    )
    source_commit = models.CharField(
        max_length=40, blank=True, null=True, default=None,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['slug']

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return f'/learning-path/{self.slug}'
