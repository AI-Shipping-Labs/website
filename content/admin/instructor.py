"""Django admin for Instructor (issue #308)."""
from django.contrib import admin

from content.models import Instructor


@admin.register(Instructor)
class InstructorAdmin(admin.ModelAdmin):
    """Admin for Instructor with searchable list and prepopulated slug."""

    list_display = [
        'name', 'instructor_id', 'status', 'has_linked_account', 'updated_at',
    ]
    list_filter = ['status']
    search_fields = ['name', 'instructor_id', 'bio', 'email']
    prepopulated_fields = {'instructor_id': ('name',)}
    raw_id_fields = ['user']
    readonly_fields = [
        'created_at', 'updated_at',
        'source_repo', 'source_path', 'source_commit',
        'bio_html',
    ]
    ordering = ['name']

    @admin.display(boolean=True, description='Linked account')
    def has_linked_account(self, obj):
        """Whether this instructor is linked to a platform user account."""
        return obj.user_id is not None

    fieldsets = (
        (None, {
            'fields': (
                'instructor_id', 'name', 'email', 'bio', 'photo_url', 'links',
                'status', 'user',
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
