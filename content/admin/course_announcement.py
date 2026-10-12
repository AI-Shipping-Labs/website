"""Forensic Django admin for course announcements (issue #1942).

Authoring lives in Studio. This ModelAdmin is list/filter/search only.
"""

from django.contrib import admin

from content.models.course_announcement import CourseAnnouncement


@admin.register(CourseAnnouncement)
class CourseAnnouncementAdmin(admin.ModelAdmin):
    list_display = [
        'title',
        'course',
        'status',
        'author',
        'published_at',
        'email_queued_at',
        'created_at',
    ]
    list_filter = ['status', 'course']
    search_fields = ['title', 'body', 'course__title', 'author__email']
    raw_id_fields = ['course', 'author']
    readonly_fields = [
        'course',
        'title',
        'body',
        'author',
        'status',
        'published_at',
        'email_queued_at',
        'created_at',
        'updated_at',
    ]
    ordering = ['-created_at']
    date_hierarchy = 'created_at'

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
