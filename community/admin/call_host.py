from django.contrib import admin

from community.models import CallHost


@admin.register(CallHost)
class CallHostAdmin(admin.ModelAdmin):
    list_display = ['name', 'slug', 'is_active', 'capacity', 'current_load', 'order']
    list_filter = ['is_active']
    search_fields = ['name', 'slug']
    ordering = ['order', 'name']
