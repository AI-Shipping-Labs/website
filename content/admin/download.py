from django.contrib import admin

from content.models import Download
from content.nav_availability import refresh_published_downloads_nav_cache
from studio.admin_links import studio_link


def publish_downloads(modeladmin, request, queryset):
    """Publish selected downloads and send notifications."""
    queryset.update(published=True)
    refresh_published_downloads_nav_cache()
    for download in queryset:
        try:
            from notifications.services import NotificationService
            NotificationService.notify('download', download.pk)
        except Exception:
            pass


publish_downloads.short_description = 'Publish selected downloads'


def unpublish_downloads(modeladmin, request, queryset):
    """Unpublish selected downloads."""
    queryset.update(published=False)
    refresh_published_downloads_nav_cache()


unpublish_downloads.short_description = 'Unpublish selected downloads'


@admin.register(Download)
class DownloadAdmin(admin.ModelAdmin):
    list_display = [
        'title', 'slug', 'file_type', 'file_size_bytes',
        'required_level', 'download_count', 'published', 'created_at',
        'studio_link',
    ]
    list_filter = ['published', 'required_level', 'file_type']
    search_fields = ['title', 'description']
    prepopulated_fields = {'slug': ('title',)}
    readonly_fields = [
        'download_count', 'created_at', 'updated_at', 'studio_link',
    ]
    actions = [publish_downloads, unpublish_downloads]

    fieldsets = (
        (None, {
            'fields': (
                'title', 'slug', 'description',
                'cover_image_url',
            ),
        }),
        ('File', {
            'fields': (
                'file_url', 'file_type', 'file_size_bytes',
            ),
        }),
        ('Tags & Visibility', {
            'fields': ('tags', 'required_level'),
        }),
        ('Publishing', {
            'fields': ('published',),
        }),
        ('Stats', {
            'fields': ('download_count', 'created_at', 'updated_at'),
        }),
        ('Studio', {
            'fields': ('studio_link',),
        }),
    )

    @admin.display(description='Studio')
    def studio_link(self, obj):
        return studio_link(
            obj,
            'studio_download_edit',
            lambda o: {'download_id': o.pk},
        )
