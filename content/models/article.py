import math
import uuid

from django.db import models
from django.urls import reverse
from django.utils import timezone

from content.access import VISIBILITY_CHOICES
from content.models.mixins import (
    SourceMetadataMixin,
    SyncedContentIdentityMixin,
    TimestampedModelMixin,
)
from content.utils.h1 import strip_leading_title_h1
from content.utils.markdown import markdown_to_plain_text, render_markdown

STATUS_CHOICES = [
    ('draft', 'Draft'),
    ('published', 'Published'),
]

PAGE_TYPE_CHOICES = [
    ('blog', 'Blog'),
    ('learning_path', 'Learning Path'),
]


class Article(
    SyncedContentIdentityMixin,
    SourceMetadataMixin,
    TimestampedModelMixin,
    models.Model,
):
    """Blog article / post."""
    title = models.CharField(max_length=300)
    slug = models.SlugField(max_length=300, unique=True)
    description = models.TextField(blank=True, default='')
    content_markdown = models.TextField(blank=True, default='')
    content_html = models.TextField(blank=True, default='')
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
            "``auto_banner_url``. Detects title drift between syncs so the "
            "banner is regenerated only when the title actually changed."
        ),
    )
    date = models.DateField()
    author = models.CharField(max_length=200, blank=True, default='')
    reading_time = models.CharField(max_length=50, blank=True, default='')
    tags = models.JSONField(default=list, blank=True)
    page_type = models.CharField(
        max_length=50, choices=PAGE_TYPE_CHOICES, default='blog',
        help_text="Determines the outer page template.",
    )
    data_json = models.JSONField(
        default=dict, blank=True,
        help_text="Stores raw frontmatter data dict for widget rendering.",
    )
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
    preview_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    source_event = models.ForeignKey(
        'events.Event',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='source_articles',
        help_text=(
            'Event that produced this article. Managed by article '
            'frontmatter during content sync.'
        ),
    )

    class Meta:
        ordering = ['-date']

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return f'/blog/{self.slug}'

    def get_preview_url(self):
        return reverse(
            'blog_preview',
            kwargs={'preview_token': self.preview_token},
        )

    def get_studio_edit_url(self):
        return f'/studio/articles/{self.pk}/edit'

    def regenerate_preview_token(self):
        self.preview_token = uuid.uuid4()
        self.save(update_fields=['preview_token'])

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
            from content.utils.linkify import linkify_urls
            # Strip the leading H1 if it duplicates the title — the page
            # template renders the title as the page heading, so an
            # author-written ``# Title`` would show up twice (issue #227).
            body_md = strip_leading_title_h1(self.content_markdown, self.title)
            html = render_markdown(body_md)
            html = replace_video_urls_in_html(html)
            self.content_html = linkify_urls(html)

        # Calculate reading time if not set
        if self.content_markdown and not self.reading_time:
            words = len(self.content_markdown.split())
            minutes = math.ceil(words / 200)
            self.reading_time = f'{minutes} min read'

        # Auto-generate excerpt from markdown if description is empty
        if not self.description and self.content_markdown:
            self.description = markdown_to_plain_text(body_md)[:200]

        # Keep status in sync with published flag.
        # The `published` boolean is the source of truth for views.
        # The `status` field provides a human-readable label.
        if self.published:
            self.status = 'published'
            if not self.published_at:
                self.published_at = timezone.now()
        else:
            self.status = 'draft'

        # When save() is called with update_fields (e.g. from update_or_create),
        # ensure derived fields are included so they get written to DB.
        update_fields = kwargs.get('update_fields')
        if update_fields is not None:
            update_fields = set(update_fields)
            if 'content_markdown' in update_fields:
                update_fields.update(['content_html', 'reading_time', 'description'])
            if 'published' in update_fields:
                update_fields.update(['status', 'published_at'])
            kwargs['update_fields'] = list(update_fields)

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
