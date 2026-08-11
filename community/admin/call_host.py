from django.contrib import admin

from community.models import CallHost


@admin.register(CallHost)
class CallHostAdmin(admin.ModelAdmin):
    list_display = ['name', 'slug', 'booking_url', 'is_active', 'order']
    list_filter = ['is_active']
    search_fields = ['name', 'slug']
    ordering = ['order', 'name']
    exclude = ['capacity', 'current_load']
