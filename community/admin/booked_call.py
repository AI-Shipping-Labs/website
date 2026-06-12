from django.contrib import admin

from community.models import BookedCall


@admin.register(BookedCall)
class BookedCallAdmin(admin.ModelAdmin):
    list_display = [
        'invitee_email', 'host', 'member', 'scheduled_at', 'status',
    ]
    list_filter = ['status', 'host']
    search_fields = ['invitee_email', 'invitee_name', 'calendly_event_uri']
    raw_id_fields = ['host', 'member']
    ordering = ['-scheduled_at']
