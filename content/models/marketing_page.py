import uuid
from urllib.parse import unquote

from django.core.exceptions import ValidationError
from django.db import models
from django.urls import Resolver404, resolve, reverse
from django.utils import timezone

from content.models.mixins import (
    SourceMetadataMixin,
    SyncedContentIdentityMixin,
    TimestampedModelMixin,
)
from content.utils.h1 import strip_leading_title_h1
from content.utils.linkify import linkify_urls
from content.utils.markdown import render_markdown

STATUS_DRAFT = 'draft'
STATUS_PUBLISHED = 'published'
STATUS_CHOICES = [
    (STATUS_DRAFT, 'Draft'),
    (STATUS_PUBLISHED, 'Published'),
]

NAV_SECTION_NONE = 'none'
NAV_SECTION_ABOUT = 'about'
NAV_SECTION_COMMUNITY = 'community'
NAV_SECTION_RESOURCES = 'resources'
NAV_SECTION_CHOICES = [
    (NAV_SECTION_NONE, 'None'),
    (NAV_SECTION_ABOUT, 'About'),
    (NAV_SECTION_COMMUNITY, 'Community'),
    (NAV_SECTION_RESOURCES, 'Resources'),
]

RESERVED_PUBLIC_PATH_PREFIXES = (
    '/',
    '/admin',
    '/accounts',
    '/account',
    '/onboarding',
    '/api',
    '/member-api',
    '/studio',
    '/pricing',
    '/events',
    '/blog',
    '/projects',
    '/resources',
    '/tutorials',
    '/downloads',
    '/interview',
    '/tags',
    '/workshops',
    '/courses',
    '/certificates',
    '/vote',
    '/notifications',
    '/subscribe',
    '/privacy',
    '/terms',
    '/impressum',
    '/activities',
    '/sprints',
    '/request-a-call',
    '/faq',
    '/marketing-pages',
)

MARKETING_PAGE_FALLBACK_URL_NAME = 'marketing_page_fallback'


def normalize_marketing_page_public_path(value):
    """Return the canonical marketing-page path or raise ``ValidationError``."""
    raw = '' if value is None else str(value).strip()
    if not raw:
        raise ValidationError('Public path is required.')
    if '?' in raw or '#' in raw:
        raise ValidationError('Public path must not include a query string or fragment.')
    if '\\' in raw:
        raise ValidationError('Public path must use forward slashes only.')
    if not raw.startswith('/'):
        raise ValidationError('Public path must start with /.')

    # Collapse only the leading slash run. Internal doubled slashes are
    # ambiguous URLs and are rejected below rather than silently rewritten.
    path = '/' + raw.lstrip('/')
    if len(path) > 1 and path.endswith('/'):
        raise ValidationError('Public path must not end with a trailing slash.')
    if '//' in path:
        raise ValidationError('Public path must not contain empty path segments.')

    decoded_segments = [unquote(segment) for segment in path.split('/')]
    if any(segment in ('.', '..') for segment in decoded_segments):
        raise ValidationError('Public path must not contain path traversal segments.')

    return path


def is_reserved_marketing_page_path(path):
    """Return True when ``path`` collides with a first-class site route."""
    normalized = normalize_marketing_page_public_path(path)
    for prefix in RESERVED_PUBLIC_PATH_PREFIXES:
        if normalized == prefix:
            return True
        if prefix != '/' and normalized.startswith(f'{prefix}/'):
            return True
    try:
        match = resolve(normalized)
    except Resolver404:
        return False
    return match.url_name != MARKETING_PAGE_FALLBACK_URL_NAME
    return False


class MarketingPage(
    SyncedContentIdentityMixin,
    SourceMetadataMixin,
    TimestampedModelMixin,
    models.Model,
):
    """Standalone public marketing/orientation page."""

    content_id = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
        help_text='Stable UUID for sync/API identity.',
    )
    title = models.CharField(max_length=300)
    public_path = models.CharField(
        max_length=255,
        unique=True,
        help_text='Canonical public URL path, e.g. /community-story.',
    )
    description = models.TextField(blank=True, default='')
    meta_description = models.TextField(blank=True, default='')
    content_markdown = models.TextField(blank=True, default='')
    content_html = models.TextField(blank=True, default='')
    cover_image_url = models.URLField(max_length=500, blank=True, default='')
    tags = models.JSONField(default=list, blank=True)
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_DRAFT,
    )
    published_at = models.DateTimeField(null=True, blank=True)
    show_in_sitemap = models.BooleanField(default=True)
    nav_section = models.CharField(
        max_length=20,
        choices=NAV_SECTION_CHOICES,
        default=NAV_SECTION_NONE,
    )
    nav_label = models.CharField(max_length=120, blank=True, default='')
    nav_order = models.IntegerField(default=0)
    preview_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

    class Meta:
        ordering = ['nav_order', 'title']

    def __str__(self):
        return self.title

    @property
    def is_published(self):
        return self.status == STATUS_PUBLISHED

    @property
    def effective_meta_description(self):
        return self.meta_description or self.description

    @property
    def nav_text(self):
        return self.nav_label or self.title

    def get_absolute_url(self):
        return self.public_path

    def get_preview_url(self):
        return reverse(
            'marketing_page_preview',
            kwargs={'preview_token': self.preview_token},
        )

    def get_studio_edit_url(self):
        return f'/studio/marketing-pages/{self.pk}/edit'

    def regenerate_preview_token(self):
        self.preview_token = uuid.uuid4()
        self.save(update_fields=['preview_token'])

    def clean(self):
        super().clean()
        try:
            self.public_path = normalize_marketing_page_public_path(self.public_path)
            if is_reserved_marketing_page_path(self.public_path):
                raise ValidationError(
                    'Public path conflicts with an existing route or reserved prefix.',
                )
        except ValidationError as exc:
            raise ValidationError({'public_path': exc.messages[0]}) from exc

        if self.status not in {STATUS_DRAFT, STATUS_PUBLISHED}:
            raise ValidationError({'status': 'Unknown marketing page status.'})
        if self.nav_section not in {
            NAV_SECTION_NONE,
            NAV_SECTION_ABOUT,
            NAV_SECTION_COMMUNITY,
            NAV_SECTION_RESOURCES,
        }:
            raise ValidationError({'nav_section': 'Unknown navigation section.'})

        duplicate = MarketingPage.objects.filter(public_path=self.public_path)
        if self.pk:
            duplicate = duplicate.exclude(pk=self.pk)
        if duplicate.exists():
            raise ValidationError({
                'public_path': 'A marketing page already uses this public path.',
            })

    def save(self, *args, **kwargs):
        from content.templatetags.video_utils import replace_video_urls_in_html
        from content.utils.tags import normalize_tags

        if not self.content_id:
            self.content_id = uuid.uuid4()
        self.tags = normalize_tags(self.tags)
        if self.content_markdown:
            body_md = strip_leading_title_h1(self.content_markdown, self.title)
            html = render_markdown(body_md)
            html = replace_video_urls_in_html(html)
            self.content_html = linkify_urls(html)
        else:
            self.content_html = ''

        if self.status == STATUS_PUBLISHED and not self.published_at:
            self.published_at = timezone.now()

        self.clean()

        update_fields = kwargs.get('update_fields')
        if update_fields is not None:
            update_fields = set(update_fields)
            if 'content_markdown' in update_fields:
                update_fields.add('content_html')
            if 'status' in update_fields:
                update_fields.add('published_at')
            update_fields.update({'updated_at'})
            kwargs['update_fields'] = list(update_fields)

        super().save(*args, **kwargs)

    def publish(self):
        self.status = STATUS_PUBLISHED
        if not self.published_at:
            self.published_at = timezone.now()
        self.save()

    def unpublish(self):
        self.status = STATUS_DRAFT
        self.save()
