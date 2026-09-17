"""Member topic pages synced from the private wiki repository (#1688).

One page per ``_wiki/<stem>.md`` file. The ``index`` page is the hub
rendered at ``/topics/``; every other page renders at ``/topics/<slug>/``.
Storage follows the package ``KnowledgeBasePage`` provenance pattern, but
the model is this site's own: gating uses the site's visibility levels
from ``content.access`` and every page defaults to Basic-and-above.
"""

from community_base.content_sync.provenance import (
    SourceProvenanceMixin,
    provenance_constraint,
)
from community_base.knowledge_base.models import SLUG_PATTERN
from django.core.validators import RegexValidator
from django.db import models

from content.access import LEVEL_BASIC, VISIBILITY_CHOICES
from topics.rendering import render_topic_body

SLUG_MAX_LENGTH = 300
TITLE_MAX_LENGTH = 300

# Denied renders expose this many leading plain-text characters of the
# body as the teaser; real topic bodies are far longer, so the teaser is
# always a strict subset of the gated content.
TEASER_MAX_CHARS = 200

# The hub page's slug: index.md renders at /topics/ and the slug is
# reserved in the page namespace (there is no /topics/index/ route).
HUB_SLUG = 'index'

STATUS_DRAFT = 'draft'
STATUS_PUBLISHED = 'published'
STATUS_CHOICES = (
    (STATUS_DRAFT, 'Draft'),
    (STATUS_PUBLISHED, 'Published'),
)

slug_validator = RegexValidator(
    SLUG_PATTERN, 'Enter a slug: letters, digits, dots, dashes or underscores.'
)


class TopicPage(SourceProvenanceMixin, models.Model):
    """One member topic page with its rendered HTML stored alongside."""

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    slug = models.CharField(
        max_length=SLUG_MAX_LENGTH,
        unique=True,
        validators=[slug_validator],
    )
    title = models.CharField(max_length=TITLE_MAX_LENGTH)
    summary = models.TextField(blank=True, default='')
    body = models.TextField(blank=True, default='')
    body_html = models.TextField(blank=True, default='', editable=False)
    # Topic slugs from the `related:` frontmatter list. Dangling slugs are
    # skipped at render time, never at sync time: a page may reference a
    # topic that has not been committed yet.
    related = models.JSONField(default=list, blank=True)
    required_level = models.PositiveIntegerField(
        choices=VISIBILITY_CHOICES,
        default=LEVEL_BASIC,
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PUBLISHED,
    )

    class Meta:
        ordering = ('slug',)
        constraints = [
            provenance_constraint(name='topics_topicpage_source_complete'),
        ]
        indexes = [
            models.Index(fields=['status'], name='topics_status_idx'),
        ]

    def __str__(self):
        return f'{self.title} ({self.slug})'

    def save(self, *args, **kwargs):
        self.body_html = render_topic_body(self.body, self.title)
        update_fields = kwargs.get('update_fields')
        if update_fields is not None:
            update_fields = set(update_fields)
            if 'body' in update_fields:
                update_fields.add('body_html')
            kwargs['update_fields'] = list(update_fields)
        super().save(*args, **kwargs)

    @property
    def is_published(self):
        return self.status == STATUS_PUBLISHED

    @property
    def is_hub(self):
        return self.slug == HUB_SLUG

    def get_absolute_url(self):
        if self.is_hub:
            return '/topics/'
        return f'/topics/{self.slug}/'

    @property
    def teaser(self):
        """A short plain-text teaser derived from the body (no full leak)."""
        from content.utils.markdown import markdown_to_plain_text

        plain = markdown_to_plain_text(self.body)
        if len(plain) <= TEASER_MAX_CHARS:
            return plain
        return plain[:TEASER_MAX_CHARS].rstrip() + '…'

    def resolved_related(self):
        """Published topic pages named by ``related``, in listed order.

        Dangling slugs (unknown, draft, or this page itself) are skipped so
        the Related-topics section never renders a link that 404s.
        """
        slugs = [str(slug) for slug in (self.related or [])]
        pages = TopicPage.objects.filter(
            slug__in=slugs,
            status=STATUS_PUBLISHED,
        ).exclude(pk=self.pk)
        by_slug = {page.slug: page for page in pages}
        return [by_slug[slug] for slug in slugs if slug in by_slug]
