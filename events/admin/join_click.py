from django.contrib import admin

from events.models import EventJoinClick


@admin.register(EventJoinClick)
class EventJoinClickAdmin(admin.ModelAdmin):
    list_display = ['event', 'user', 'clicked_at']
    list_filter = ['event']
    search_fields = ['user__email', 'event__title']
    readonly_fields = ['event', 'user', 'clicked_at']
