import uuid

from django.db import models


POSITION_CHOICES = [
    ('after_content', 'After Content'),
    ('sidebar', 'Sidebar'),
]


class TagRule(models.Model):
    """Admin-configurable rule for injecting components based on content tags.

    When a content detail page is rendered and its tags match a TagRule's tag,
    the configured component is injected at the specified position.

    Example: Any article tagged "ai-engineer" gets a course promo card
    after the article body.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tag = models.CharField(
        max_length=200,
        help_text='Tag to match (e.g. "ai-engineer"). Must be normalized: lowercase, hyphenated.',
    )
    component_type = models.CharField(
        max_length=200,
        help_text='Component type identifier (e.g. "course_promo", "download_cta", "roadmap_signup").',
    )
    component_config = models.JSONField(
        default=dict,
        blank=True,
        help_text='JSON configuration for the component (e.g. {"course_slug": "python-data-ai", "cta_text": "Start learning"}).',
    )
    position = models.CharField(
        max_length=20,
        choices=POSITION_CHOICES,
        default='after_content',
        help_text='Where to inject the component on the detail page.',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['tag', 'position']

    def __str__(self):
        return f'{self.tag} -> {self.component_type} ({self.position})'

    def save(self, *args, **kwargs):
        # Normalize the tag on save
        from content.utils.tags import normalize_tag
        self.tag = normalize_tag(self.tag)
        super().save(*args, **kwargs)
