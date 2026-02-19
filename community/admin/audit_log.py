from django.contrib import admin

from community.models import CommunityAuditLog


@admin.register(CommunityAuditLog)
class CommunityAuditLogAdmin(admin.ModelAdmin):
    list_display = ["user", "action", "timestamp"]
    list_filter = ["action", "timestamp"]
    search_fields = ["user__email", "details"]
    readonly_fields = ["user", "action", "timestamp", "details"]
    ordering = ["-timestamp"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
