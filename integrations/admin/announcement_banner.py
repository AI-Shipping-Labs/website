from django.contrib import admin

from integrations.middleware import clear_announcement_banner_cache
from integrations.models import AnnouncementBanner


@admin.register(AnnouncementBanner)
class AnnouncementBannerAdmin(admin.ModelAdmin):
    list_display = ['message', 'is_enabled', 'is_dismissible', 'version', 'updated_at']
    list_filter = ['is_enabled', 'is_dismissible']
    readonly_fields = ['version', 'updated_at']

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        clear_announcement_banner_cache()
