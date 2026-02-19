from django.contrib import admin
from django.utils import timezone

from events.models import Event, EventRegistration


def make_upcoming(modeladmin, request, queryset):
    """Transition selected events to upcoming status."""
    queryset.filter(status='draft').update(status='upcoming')


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


@admin.register(EventRegistration)
class EventRegistrationAdmin(admin.ModelAdmin):
    list_display = ['event', 'user', 'registered_at']
    list_filter = ['event']
    search_fields = ['user__email', 'event__title']
    readonly_fields = ['registered_at']
