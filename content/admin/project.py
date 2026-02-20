from django.contrib import admin
from django.utils import timezone

from content.models import Project


def approve_projects(modeladmin, request, queryset):
    """Approve selected projects (publish them)."""
    queryset.update(
        status='published',
        published=True,
        published_at=timezone.now(),
    )


approve_projects.short_description = 'Approve selected projects (publish)'


def reject_projects(modeladmin, request, queryset):
    """Reject selected projects (set to pending_review, unpublish)."""
    queryset.update(
        status='pending_review',
        published=False,
    )


reject_projects.short_description = 'Reject selected projects (unpublish)'


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = [
        'title', 'slug', 'status', 'author', 'difficulty',
        'date', 'required_level', 'published', 'published_at',
    ]
    list_filter = ['status', 'published', 'required_level', 'difficulty', 'date']
    search_fields = ['title', 'description']
    prepopulated_fields = {'slug': ('title',)}
    actions = [approve_projects, reject_projects]
    readonly_fields = ['published_at']

    fieldsets = (
        (None, {
            'fields': (
                'title', 'slug', 'author', 'description',
                'content_markdown', 'cover_image_url',
            ),
        }),
        ('Project Details', {
            'fields': (
                'difficulty', 'tags', 'source_code_url', 'demo_url',
            ),
        }),
        ('Access & Visibility', {
            'fields': ('required_level',),
        }),
        ('Publishing', {
            'fields': ('published', 'status', 'date', 'published_at', 'submitter'),
        }),
    )
