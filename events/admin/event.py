import json

from django import forms
from django.contrib import admin
from django.utils import timezone

from content.admin.widgets import TimestampEditorWidget
from events.models import Event, EventInstructor, EventRegistration


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


def make_completed(modeladmin, request, queryset):
    """Transition selected events to completed status."""
    queryset.filter(status='upcoming').update(status='completed')


make_completed.short_description = 'Set status to Completed'


def make_cancelled(modeladmin, request, queryset):
    """Cancel selected events (from any state)."""
    queryset.update(status='cancelled')


make_cancelled.short_description = 'Cancel selected events'


def publish_recordings(modeladmin, request, queryset):
    """Publish selected events as recordings and send notifications."""
    queryset.update(published=True, published_at=timezone.now())
    for event in queryset:
        try:
            from notifications.services import NotificationService
            NotificationService.notify('recording', event.pk)
        except Exception:
            pass


publish_recordings.short_description = 'Publish selected as recordings'


def unpublish_recordings(modeladmin, request, queryset):
    """Unpublish selected events as recordings."""
    queryset.update(published=False, published_at=None)


unpublish_recordings.short_description = 'Unpublish selected recordings'


class EventAdminForm(forms.ModelForm):
    """Custom form for Event that uses the TimestampEditorWidget."""

    class Meta:
        model = Event
        fields = '__all__'
        widgets = {
            'timestamps': TimestampEditorWidget(),
        }

    def clean_timestamps(self):
        """Parse the JSON string back into a Python list."""
        value = self.cleaned_data.get('timestamps', '[]')
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                if isinstance(parsed, list):
                    return parsed
            except (json.JSONDecodeError, TypeError):
                pass
        return []


class EventRegistrationInline(admin.TabularInline):
    model = EventRegistration
    extra = 0
    readonly_fields = ['user', 'registered_at']
    can_delete = True


class EventInstructorInline(admin.TabularInline):
    """Inline editor for Event-Instructor through rows with ordering."""
    model = EventInstructor
    extra = 0
    ordering = ['position']
    fields = ['instructor', 'position']
    raw_id_fields = ['instructor']


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    form = EventAdminForm
    list_display = [
        'title', 'slug', 'platform', 'external_host', 'status',
        'start_datetime', 'required_level', 'has_recording_display',
        'registration_count_display',
    ]
    list_filter = ['status', 'platform', 'external_host', 'required_level', 'published']
    search_fields = ['title', 'description']
    prepopulated_fields = {'slug': ('title',)}
    actions = [
        make_upcoming, make_completed, make_cancelled,
        publish_recordings, unpublish_recordings,
    ]
    inlines = [EventInstructorInline, EventRegistrationInline]

    fieldsets = (
        (None, {
            'fields': (
                'title', 'slug', 'description', 'platform', 'external_host',
            ),
        }),
        ('Schedule', {
            'fields': (
                'start_datetime', 'end_datetime', 'timezone', 'location',
            ),
        }),
        ('Join Details', {
            'fields': ('zoom_meeting_id', 'zoom_join_url'),
            'classes': ('collapse',),
        }),
        ('Access & Capacity', {
            'fields': ('tags', 'required_level', 'max_participants'),
        }),
        ('Status', {
            'fields': ('status',),
        }),
        ('Recording', {
            'fields': (
                'recording_url', 'recording_s3_url', 'recording_embed_url',
                'transcript_url', 'transcript_text',
                'timestamps', 'materials', 'core_tools',
                'learning_objectives', 'outcome',
                'cover_image_url',
                'published', 'published_at',
            ),
            'classes': ('collapse',),
        }),
        ('Content Sync', {
            'fields': (
                'content_id', 'source_repo', 'source_path', 'source_commit',
                'related_course',
            ),
            'classes': ('collapse',),
        }),
    )

    readonly_fields = ['published_at']

    def has_recording_display(self, obj):
        return bool(obj.recording_url)

    has_recording_display.short_description = 'Recording'
    has_recording_display.boolean = True

    def registration_count_display(self, obj):
        count = obj.registration_count
        if obj.max_participants:
            return f'{count}/{obj.max_participants}'
        return str(count)

    registration_count_display.short_description = 'Registrations'

    def save_model(self, request, obj, form, change):
        """Save the event. Zoom meeting creation is handled via Studio."""
        super().save_model(request, obj, form, change)


@admin.register(EventRegistration)
class EventRegistrationAdmin(admin.ModelAdmin):
    list_display = ['event', 'user', 'registered_at']
    list_filter = ['event']
    search_fields = ['user__email', 'event__title']
    readonly_fields = ['registered_at']
