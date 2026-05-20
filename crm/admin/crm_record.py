from django.contrib import admin

from crm.models import CRMRecord
from studio.admin_links import studio_link


@admin.register(CRMRecord)
class CRMRecordAdmin(admin.ModelAdmin):
    list_display = (
        'user', 'status', 'persona', 'created_at', 'updated_at',
        'studio_link',
    )
    list_filter = ('status',)
    search_fields = ('user__email', 'persona')
    raw_id_fields = ('user', 'created_by')
    readonly_fields = ('studio_link',)

    @admin.display(description='Studio')
    def studio_link(self, obj):
        return studio_link(
            obj,
            'studio_crm_detail',
            lambda o: {'crm_id': o.pk},
        )
