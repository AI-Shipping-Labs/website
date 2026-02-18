from django.contrib import admin
from content.models import CuratedLink


@admin.register(CuratedLink)
class CuratedLinkAdmin(admin.ModelAdmin):
    list_display = ['title', 'category', 'sort_order', 'required_level', 'published']
    list_filter = ['published', 'required_level', 'category']
    search_fields = ['title', 'description']
