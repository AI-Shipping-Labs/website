"""Django admin for Instructor (issue #308)."""
from django.contrib import admin

from content.models import Instructor


@admin.register(Instructor)
class InstructorAdmin(admin.ModelAdmin):
    """Admin for Instructor with searchable list and prepopulated slug."""

    list_display = ['name', 'instructor_id', 'status', 'updated_at']
    list_filter = ['status']
    search_fields = ['name', 'instructor_id', 'bio']
    prepopulated_fields = {'instructor_id': ('name',)}
    readonly_fields = [
        'created_at', 'updated_at',
        'source_repo', 'source_path', 'source_commit',
        'bio_html',
    ]
    ordering = ['name']

    fieldsets = (
        (None, {
            'fields': (
                'instructor_id', 'name', 'bio', 'photo_url', 'links',
                'status',
            ),
        }),
        ('Source / Timestamps', {
            'fields': (
                'source_repo', 'source_path', 'source_commit',
                'created_at', 'updated_at',
            ),
            'classes': ('collapse',),
        }),
    )
