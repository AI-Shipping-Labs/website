from django.contrib import admin

from content.models import Download


@admin.register(Download)
class DownloadAdmin(admin.ModelAdmin):
    list_display = [
        'title', 'slug', 'file_type', 'file_size_bytes',
        'required_level', 'download_count', 'published', 'created_at',
    ]
    list_filter = ['published', 'required_level', 'file_type']
    search_fields = ['title', 'description']
    prepopulated_fields = {'slug': ('title',)}
    readonly_fields = ['download_count', 'created_at', 'updated_at']

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
    )
