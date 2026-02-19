from django.contrib import admin
from django.utils import timezone

from content.models import Article


def publish_articles(modeladmin, request, queryset):
    """Publish selected articles and send notifications."""
    queryset.update(
        status='published',
        published=True,
        published_at=timezone.now(),
    )
    for article in queryset:
        try:
            from notifications.services import NotificationService
            NotificationService.notify('article', article.pk)
        except Exception:
            pass


publish_articles.short_description = 'Publish selected articles'


def unpublish_articles(modeladmin, request, queryset):
    """Unpublish selected articles (set to draft)."""
    queryset.update(
        status='draft',
        published=False,
    )


unpublish_articles.short_description = 'Unpublish selected articles'


@admin.register(Article)
class ArticleAdmin(admin.ModelAdmin):
    list_display = [
        'title', 'slug', 'status', 'author', 'date',
        'required_level', 'published_at',
    ]
    list_filter = ['status', 'published', 'required_level', 'date']
    search_fields = ['title', 'description', 'content_markdown']
    prepopulated_fields = {'slug': ('title',)}
    actions = [publish_articles, unpublish_articles]

    fieldsets = (
        (None, {
            'fields': (
                'title', 'slug', 'author', 'description',
                'cover_image_url', 'content_markdown',
            ),
        }),
        ('Tags & Visibility', {
            'fields': ('tags', 'required_level'),
        }),
        ('Publishing', {
            'fields': ('published', 'date', 'published_at'),
        }),
    )

    readonly_fields = ['published_at']
