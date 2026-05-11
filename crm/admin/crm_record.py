from django.contrib import admin

from crm.models import CRMExperiment, CRMRecord


@admin.register(CRMRecord)
class CRMRecordAdmin(admin.ModelAdmin):
    list_display = ('user', 'status', 'persona', 'created_at', 'updated_at')
    list_filter = ('status',)
    search_fields = ('user__email', 'persona')
    raw_id_fields = ('user', 'created_by')


@admin.register(CRMExperiment)
class CRMExperimentAdmin(admin.ModelAdmin):
    list_display = ('crm_record', 'title', 'status', 'created_at')
    list_filter = ('status',)
    search_fields = ('crm_record__user__email', 'title')
    raw_id_fields = ('crm_record',)
