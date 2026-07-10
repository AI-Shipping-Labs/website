from django.contrib import admin

from content.models import MarketingPage


@admin.register(MarketingPage)
class MarketingPageAdmin(admin.ModelAdmin):
    list_display = (
        'title',
        'public_path',
        'status',
        'nav_section',
        'source_repo',
        'updated_at',
    )
    list_filter = ('status', 'nav_section', 'show_in_sitemap')
    search_fields = ('title', 'public_path', 'description', 'source_path')
    readonly_fields = ('content_id', 'content_html', 'preview_token', 'created_at', 'updated_at')
