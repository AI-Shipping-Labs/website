from django.contrib import admin

from integrations.models import Redirect
from studio.admin_links import studio_link


@admin.register(Redirect)
class RedirectAdmin(admin.ModelAdmin):
    list_display = [
        'source_path', 'target_path', 'redirect_type', 'is_active',
        'updated_at', 'studio_link',
    ]
    list_filter = ['redirect_type', 'is_active']
    search_fields = ['source_path', 'target_path']
    list_editable = ['is_active']
    readonly_fields = ['studio_link']

    @admin.display(description='Studio')
    def studio_link(self, obj):
        return studio_link(
            obj,
            'studio_redirect_edit',
            lambda o: {'redirect_id': o.pk},
        )
