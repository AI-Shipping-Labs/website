from django.contrib import admin
from content.models import CuratedLink


@admin.register(CuratedLink)
class CuratedLinkAdmin(admin.ModelAdmin):
    list_display = ['title', 'category', 'sort_order', 'required_level', 'published']
    list_filter = ['published', 'required_level', 'category']
    search_fields = ['title', 'description']

    fieldsets = (
        (None, {
            'fields': (
                'item_id', 'title', 'description', 'url', 'category',
            ),
        }),
        ('Tags & Visibility', {
            'fields': ('tags', 'required_level'),
        }),
        ('Ordering & Publishing', {
            'fields': ('sort_order', 'source', 'published'),
        }),
    )
