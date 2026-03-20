from django.contrib import admin

from integrations.models import Redirect


@admin.register(Redirect)
class RedirectAdmin(admin.ModelAdmin):
    list_display = ['source_path', 'target_path', 'redirect_type', 'is_active', 'updated_at']
    list_filter = ['redirect_type', 'is_active']
    search_fields = ['source_path', 'target_path']
    list_editable = ['is_active']
