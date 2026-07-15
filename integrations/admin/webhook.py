from django.contrib import admin

from integrations.models import WebhookLog


@admin.register(WebhookLog)
class WebhookLogAdmin(admin.ModelAdmin):
    list_display = ('service', 'event_type', 'processed', 'attempts', 'received_at')
    list_filter = ('service', 'processed', 'event_type')
    readonly_fields = (
        'service', 'event_type', 'payload', 'deduplication_key', 'attempts',
        'error_message', 'received_at', 'processed_at',
    )
    search_fields = ('event_type', 'deduplication_key', 'error_message')
