from django.contrib import admin

from crm.models import CRMRecord


@admin.register(CRMRecord)
class CRMRecordAdmin(admin.ModelAdmin):
    list_display = ('user', 'status', 'persona', 'created_at', 'updated_at')
    list_filter = ('status',)
    search_fields = ('user__email', 'persona')
    raw_id_fields = ('user', 'created_by')
