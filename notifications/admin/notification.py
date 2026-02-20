from django.contrib import admin

from notifications.models import Notification, EventReminderLog


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = [
        'title', 'user', 'notification_type', 'read', 'url', 'created_at',
    ]
    list_filter = ['notification_type', 'read', 'created_at']
    search_fields = ['title', 'body', 'user__email']
    readonly_fields = ['created_at']
    raw_id_fields = ['user']

    fieldsets = (
        (None, {
            'fields': ('user', 'title', 'body', 'url'),
        }),
        ('Type & Status', {
            'fields': ('notification_type', 'read'),
        }),
        ('Timestamps', {
            'fields': ('created_at',),
        }),
    )


@admin.register(EventReminderLog)
class EventReminderLogAdmin(admin.ModelAdmin):
    list_display = ['event', 'user', 'interval', 'created_at']
    list_filter = ['interval', 'created_at']
    search_fields = ['event__title', 'user__email']
    raw_id_fields = ['event', 'user']
