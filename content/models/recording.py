from django.db import models


class Recording(models.Model):
    """Event recording / workshop resource."""
    title = models.CharField(max_length=300)
    slug = models.SlugField(max_length=300, unique=True)
    description = models.TextField(blank=True, default='')
    date = models.DateField()
    tags = models.JSONField(default=list, blank=True)
    level = models.CharField(max_length=100, blank=True, default='')
    google_embed_url = models.URLField(max_length=500, blank=True, default='')
    youtube_url = models.URLField(max_length=500, blank=True, default='')
    timestamps = models.JSONField(default=list, blank=True)
    materials = models.JSONField(default=list, blank=True)
    core_tools = models.JSONField(default=list, blank=True)
    learning_objectives = models.JSONField(default=list, blank=True)
    outcome = models.TextField(blank=True, default='')
    related_course = models.CharField(max_length=300, blank=True, default='')
    published = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-date']

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return f'/event-recordings/{self.slug}'

    def formatted_date(self):
        return self.date.strftime('%B %d, %Y')

    def short_date(self):
        return self.date.strftime('%b %d, %Y')
