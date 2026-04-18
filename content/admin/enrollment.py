"""Django admin for Enrollment — issue #236.

Studio surface lives in ``studio/views/enrollments.py``. This admin is
the low-level staff fallback (Django admin) for direct table edits.
"""

from django.contrib import admin

from content.models import Enrollment


@admin.register(Enrollment)
class EnrollmentAdmin(admin.ModelAdmin):
    list_display = ['user', 'course', 'source', 'enrolled_at', 'unenrolled_at']
    list_filter = ['source', 'unenrolled_at']
    search_fields = ['user__email', 'course__title']
    raw_id_fields = ['user', 'course']
    readonly_fields = ['enrolled_at']
