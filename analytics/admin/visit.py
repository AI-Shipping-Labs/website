"""Read-only Django admin for CampaignVisit."""

from django.contrib import admin

from analytics.models import CampaignVisit


@admin.register(CampaignVisit)
class CampaignVisitAdmin(admin.ModelAdmin):
    list_display = ['ts', 'utm_source', 'utm_medium', 'utm_campaign', 'path', 'user']
    list_filter = ['utm_source', 'utm_medium']
    search_fields = ['utm_campaign', 'utm_content', 'utm_source', 'utm_medium', 'path', 'anonymous_id']
    readonly_fields = [
        'campaign', 'utm_source', 'utm_medium', 'utm_campaign', 'utm_content',
        'utm_term', 'path', 'referrer', 'user_agent', 'ip_hash', 'anonymous_id',
        'user', 'ts',
    ]
    date_hierarchy = 'ts'
    list_select_related = ['campaign', 'user']

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        # Allow viewing the change form (read-only fields), but block edits.
        return request.user.is_superuser if obj is None else False

    def has_delete_permission(self, request, obj=None):
        # Allow superusers to clean up bot/test pollution.
        return request.user.is_superuser


__all__ = ['CampaignVisitAdmin']
