from django.contrib import admin

from integrations.models import ContentSource, SyncLog


class SyncLogInline(admin.TabularInline):
    model = SyncLog
    extra = 0
    readonly_fields = [
        'started_at', 'finished_at', 'status',
        'items_created', 'items_updated', 'items_deleted', 'errors',
    ]
    ordering = ['-started_at']
    max_num = 10
    can_delete = False


@admin.register(ContentSource)
class ContentSourceAdmin(admin.ModelAdmin):
    list_display = [
        'repo_name', 'content_type', 'is_private',
        'last_synced_at', 'last_sync_status',
    ]
    list_filter = ['content_type', 'is_private', 'last_sync_status']
    search_fields = ['repo_name']
    readonly_fields = ['id', 'last_synced_at', 'last_sync_status', 'last_sync_log']
    inlines = [SyncLogInline]


@admin.register(SyncLog)
class SyncLogAdmin(admin.ModelAdmin):
    list_display = [
        'source', 'status', 'started_at', 'finished_at',
        'items_created', 'items_updated', 'items_deleted',
    ]
    list_filter = ['status', 'source']
    readonly_fields = [
        'id', 'source', 'started_at', 'finished_at', 'status',
        'items_created', 'items_updated', 'items_deleted', 'errors',
    ]
    ordering = ['-started_at']
