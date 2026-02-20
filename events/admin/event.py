import logging

from django.contrib import admin, messages
from django.utils import timezone

from events.models import Event, EventRegistration

logger = logging.getLogger(__name__)


def make_upcoming(modeladmin, request, queryset):
    """Transition selected events to upcoming status and send notifications."""
    draft_events = list(queryset.filter(status='draft'))
    queryset.filter(status='draft').update(status='upcoming')
    for event in draft_events:
        try:
            from notifications.services import NotificationService
            NotificationService.notify('event', event.pk)
        except Exception:
            pass


make_upcoming.short_description = 'Set status to Upcoming'


def make_live(modeladmin, request, queryset):
    """Transition selected events to live status."""
    queryset.filter(status='upcoming').update(status='live')


make_live.short_description = 'Set status to Live'


def make_completed(modeladmin, request, queryset):
    """Transition selected events to completed status."""
    queryset.filter(status__in=['upcoming', 'live']).update(status='completed')


make_completed.short_description = 'Set status to Completed'


def make_cancelled(modeladmin, request, queryset):
    """Cancel selected events (from any state)."""
    queryset.update(status='cancelled')


make_cancelled.short_description = 'Cancel selected events'


class EventRegistrationInline(admin.TabularInline):
    model = EventRegistration
    extra = 0
    readonly_fields = ['user', 'registered_at']
    can_delete = True


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = [
        'title', 'slug', 'event_type', 'status',
        'start_datetime', 'required_level', 'registration_count_display',
    ]
    list_filter = ['status', 'event_type', 'required_level']
    search_fields = ['title', 'description']
    prepopulated_fields = {'slug': ('title',)}
    actions = [make_upcoming, make_live, make_completed, make_cancelled]
    inlines = [EventRegistrationInline]

    fieldsets = (
        (None, {
            'fields': (
                'title', 'slug', 'description', 'event_type',
            ),
        }),
        ('Schedule', {
            'fields': (
                'start_datetime', 'end_datetime', 'timezone', 'location',
            ),
        }),
        ('Zoom', {
            'fields': ('zoom_meeting_id', 'zoom_join_url'),
            'classes': ('collapse',),
        }),
        ('Access & Capacity', {
            'fields': ('tags', 'required_level', 'max_participants'),
        }),
        ('Status & Recording', {
            'fields': ('status', 'recording'),
        }),
    )

    def registration_count_display(self, obj):
        count = obj.registration_count
        if obj.max_participants:
            return f'{count}/{obj.max_participants}'
        return str(count)

    registration_count_display.short_description = 'Registrations'

    def save_model(self, request, obj, form, change):
        """Save the event and auto-create a Zoom meeting for new live events.

        When a new event is created (not edited) with event_type='live'
        and no zoom_meeting_id already set, this calls the Zoom API to
        create a meeting and stores the meeting ID and join URL.
        """
        is_new = not change
        is_live = obj.event_type == 'live'
        has_no_zoom = not obj.zoom_meeting_id

        # Save the event first so it has a PK
        super().save_model(request, obj, form, change)

        # Auto-create Zoom meeting for new live events
        if is_new and is_live and has_no_zoom:
            try:
                from integrations.services.zoom import create_meeting
                result = create_meeting(obj)
                obj.zoom_meeting_id = result['meeting_id']
                obj.zoom_join_url = result['join_url']
                obj.save(update_fields=['zoom_meeting_id', 'zoom_join_url'])
                messages.success(
                    request,
                    f'Zoom meeting created: {result["join_url"]}',
                )
            except Exception as e:
                logger.exception('Failed to create Zoom meeting for event %s', obj.slug)
                messages.warning(
                    request,
                    f'Event saved, but Zoom meeting creation failed: {e}. '
                    f'You can add the Zoom details manually.',
                )


@admin.register(EventRegistration)
class EventRegistrationAdmin(admin.ModelAdmin):
    list_display = ['event', 'user', 'registered_at']
    list_filter = ['event']
    search_fields = ['user__email', 'event__title']
    readonly_fields = ['registered_at']
