from django.contrib import admin

from crm.models import Call
from studio.admin_links import studio_link


@admin.register(Call)
class CallAdmin(admin.ModelAdmin):
    """Admin for the CRM call log (issue #1949)."""

    list_display = (
        'email', 'occurred_at', 'source', 'created_by', 'created_at',
        'studio_link',
    )
    list_filter = ('source',)
    search_fields = ('email', 'contact__user__email', 'summary')
    raw_id_fields = ('contact', 'created_by')
    readonly_fields = ('studio_link',)

    @admin.display(description='Studio')
    def studio_link(self, obj):
        return studio_link(
            obj,
            'studio_crm_call_detail',
            lambda o: {'call_id': o.pk},
        )
