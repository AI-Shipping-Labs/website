"""Django admin for Workshop + WorkshopPage (issue #295)."""
from django.contrib import admin

from content.models import Workshop, WorkshopInstructor, WorkshopPage


class WorkshopPageInline(admin.StackedInline):
    """Inline editor for workshop pages on the Workshop admin form."""
    model = WorkshopPage
    extra = 0
    ordering = ['sort_order']
    fields = ['title', 'slug', 'sort_order', 'body']
    classes = ['collapse']


class WorkshopInstructorInline(admin.TabularInline):
    """Inline editor for Workshop-Instructor through rows with ordering."""
    model = WorkshopInstructor
    extra = 0
    ordering = ['position']
    fields = ['instructor', 'position']
    raw_id_fields = ['instructor']


@admin.register(Workshop)
class WorkshopAdmin(admin.ModelAdmin):
    """Admin for Workshop with nested page inline editor."""

    list_display = [
        'title', 'slug', 'date', 'status',
        'landing_required_level',
        'pages_required_level', 'recording_required_level',
        'event', 'updated_at',
    ]
    list_display_links = ['title']
    list_filter = [
        'status', 'landing_required_level',
        'pages_required_level', 'recording_required_level',
    ]
    search_fields = ['title', 'slug', 'description']
    prepopulated_fields = {'slug': ('title',)}
    raw_id_fields = ['event']
    inlines = [WorkshopInstructorInline, WorkshopPageInline]
    ordering = ['-date']
    readonly_fields = ['created_at', 'updated_at']

    fieldsets = (
        (None, {
            'fields': (
                'title', 'slug', 'description', 'cover_image_url',
                'instructor_name', 'date', 'tags',
            ),
        }),
        ('Access', {
            'fields': (
                'status', 'landing_required_level',
                'pages_required_level', 'recording_required_level',
            ),
        }),
        ('Linking', {
            'fields': ('event', 'code_repo_url'),
        }),
        ('Source / Timestamps', {
            'fields': (
                'source_repo', 'source_path', 'source_commit',
                'created_at', 'updated_at',
            ),
            'classes': ('collapse',),
        }),
    )


@admin.register(WorkshopPage)
class WorkshopPageAdmin(admin.ModelAdmin):
    """Admin for WorkshopPage (accessible directly, plus as inline)."""

    list_display = ['title', 'workshop', 'sort_order', 'slug']
    list_filter = ['workshop']
    search_fields = ['title', 'body']
    ordering = ['workshop', 'sort_order']
    raw_id_fields = ['workshop']
