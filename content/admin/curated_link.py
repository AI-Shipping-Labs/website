from django.contrib import admin
from content.models import CuratedLink


@admin.register(CuratedLink)
class CuratedLinkAdmin(admin.ModelAdmin):
    list_display = ['title', 'category', 'sort_order', 'published']
    list_filter = ['published', 'category']
    search_fields = ['title', 'description']
