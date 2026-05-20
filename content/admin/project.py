from django.contrib import admin
from django.utils import timezone

from content.models import Project
from studio.admin_links import studio_link


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
        'studio_link',
    ]
    list_filter = ['status', 'published', 'required_level', 'difficulty', 'date']
    search_fields = ['title', 'description']
    prepopulated_fields = {'slug': ('title',)}
    actions = [approve_projects, reject_projects]
    readonly_fields = ['published_at', 'studio_link']

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
        ('Studio', {
            'fields': ('studio_link',),
        }),
    )

    @admin.display(description='Studio')
    def studio_link(self, obj):
        return studio_link(
            obj,
            'studio_project_review',
            lambda o: {'project_id': o.pk},
        )
