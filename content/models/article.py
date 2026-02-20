import math

import markdown

from django.db import models
from django.utils import timezone

from content.access import VISIBILITY_CHOICES


def render_markdown(text):
    """Convert markdown to HTML with syntax highlighting."""
    return markdown.markdown(
        text,
        extensions=[
            'fenced_code',
            'codehilite',
            'tables',
            'attr_list',
            'md_in_html',
        ],
        extension_configs={
            'codehilite': {
                'css_class': 'codehilite',
                'guess_lang': False,
            },
        },
    )


STATUS_CHOICES = [
    ('draft', 'Draft'),
    ('published', 'Published'),
]


class Article(models.Model):
    """Blog article / post."""
    title = models.CharField(max_length=300)
    slug = models.SlugField(max_length=300, unique=True)
    description = models.TextField(blank=True, default='')
    content_markdown = models.TextField(blank=True, default='')
    content_html = models.TextField(blank=True, default='')
    cover_image_url = models.URLField(max_length=500, blank=True, default='')
    date = models.DateField()
    author = models.CharField(max_length=200, blank=True, default='')
    reading_time = models.CharField(max_length=50, blank=True, default='')
    tags = models.JSONField(default=list, blank=True)
    required_level = models.IntegerField(
        default=0,
        choices=VISIBILITY_CHOICES,
        help_text="Minimum tier level required to view full content.",
    )
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default='draft',
    )
    published = models.BooleanField(default=True)
    published_at = models.DateTimeField(null=True, blank=True)
    source_repo = models.CharField(
        max_length=300, blank=True, null=True, default=None,
        help_text="GitHub repo this content was synced from (e.g. AI-Shipping-Labs/blog).",
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
        ordering = ['-date']

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return f'/blog/{self.slug}'

    def formatted_date(self):
        return self.date.strftime('%B %d, %Y')

    def short_date(self):
        return self.date.strftime('%b %d, %Y')

    def save(self, *args, **kwargs):
        # Normalize tags on save
        from content.utils.tags import normalize_tags
        self.tags = normalize_tags(self.tags)

        # Auto-render markdown to HTML on save
        if self.content_markdown:
            from content.templatetags.video_utils import replace_video_urls_in_html
            html = render_markdown(self.content_markdown)
            self.content_html = replace_video_urls_in_html(html)

        # Calculate reading time if not set
        if self.content_markdown and not self.reading_time:
            words = len(self.content_markdown.split())
            minutes = math.ceil(words / 200)
            self.reading_time = f'{minutes} min read'

        # Auto-generate excerpt from markdown if description is empty
        if not self.description and self.content_markdown:
            self.description = self.content_markdown[:200]

        # Keep status in sync with published flag.
        # The `published` boolean is the source of truth for views.
        # The `status` field provides a human-readable label.
        if self.published:
            self.status = 'published'
            if not self.published_at:
                self.published_at = timezone.now()
        else:
            self.status = 'draft'

        super().save(*args, **kwargs)

    def publish(self):
        """Publish this article, setting published_at to now."""
        self.published = True
        self.status = 'published'
        self.published_at = timezone.now()
        self.save()

    def unpublish(self):
        """Unpublish this article (set to draft)."""
        self.published = False
        self.status = 'draft'
        self.save()

    def get_related_articles(self, limit=3):
        """Return published articles that share at least one tag."""
        if not self.tags:
            return Article.objects.none()
        # Find articles with overlapping tags
        related = Article.objects.filter(
            published=True,
        ).exclude(pk=self.pk)
        # Filter by shared tags using JSON containment
        # Since tags is a JSONField list, we filter manually
        matching = []
        for article in related:
            if article.tags and set(article.tags) & set(self.tags):
                matching.append(article.pk)
        return Article.objects.filter(pk__in=matching[:limit]).order_by('-date')
