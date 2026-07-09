"""Django admin for Workshop + WorkshopPage (issue #295)."""
from django.contrib import admin

from content.models import Workshop, WorkshopInstructor, WorkshopPage
from studio.admin_links import studio_link


class WorkshopPageInline(admin.StackedInline):
    """Inline editor for workshop pages on the Workshop admin form."""
    model = WorkshopPage
    extra = 0
    ordering = ['sort_order']
    fields = ['title', 'slug', 'sort_order', 'required_level', 'body']
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
        'title', 'slug', 'date', 'status', 'skill_level',
        'landing_required_level',
        'pages_required_level', 'recording_required_level',
        'event', 'updated_at', 'studio_link',
    ]
    list_display_links = ['title']
    list_filter = [
        'status', 'skill_level', 'landing_required_level',
        'pages_required_level', 'recording_required_level',
    ]
    search_fields = ['title', 'slug', 'description', 'skill_level']
    prepopulated_fields = {'slug': ('title',)}
    raw_id_fields = ['event']
    inlines = [WorkshopInstructorInline, WorkshopPageInline]
    ordering = ['-date']
    readonly_fields = [
        'skill_level', 'created_at', 'updated_at', 'studio_link',
    ]

    fieldsets = (
        (None, {
            'fields': (
                'title', 'slug', 'description', 'cover_image_url',
                'date', 'tags', 'skill_level',
            ),
        }),
        ('Access', {
            'fields': (
                'status', 'landing_required_level',
                'pages_required_level', 'recording_required_level',
            ),
        }),
        ('Linking', {
            'fields': ('event', 'code_repo_url', 'materials'),
        }),
        ('Source / Timestamps', {
            'fields': (
                'source_repo', 'source_path', 'source_commit',
                'created_at', 'updated_at',
            ),
            'classes': ('collapse',),
        }),
        ('Studio', {
            'fields': ('studio_link',),
        }),
    )

    @admin.display(description='Studio')
    def studio_link(self, obj):
        return studio_link(
            obj,
            'studio_workshop_detail',
            lambda o: {'workshop_id': o.pk},
        )


@admin.register(WorkshopPage)
class WorkshopPageAdmin(admin.ModelAdmin):
    """Admin for WorkshopPage (accessible directly, plus as inline)."""

    list_display = [
        'title', 'workshop', 'sort_order', 'slug', 'required_level',
    ]
    list_filter = ['workshop', 'required_level']
    search_fields = ['title', 'body']
    ordering = ['workshop', 'sort_order']
    raw_id_fields = ['workshop']
