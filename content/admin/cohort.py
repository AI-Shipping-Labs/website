from django.contrib import admin

from content.models import Cohort, CohortEnrollment


class CohortEnrollmentInline(admin.TabularInline):
    """Inline editor for enrollments within a cohort."""
    model = CohortEnrollment
    extra = 0
    raw_id_fields = ['user']
    readonly_fields = ['enrolled_at']


@admin.register(Cohort)
class CohortAdmin(admin.ModelAdmin):
    """Admin for Cohort with enrollment inline."""

    list_display = [
        'name', 'course', 'start_date', 'end_date',
        'is_active', 'max_participants', 'enrollment_count',
    ]
    list_display_links = ['name']
    list_filter = ['is_active', 'course']
    search_fields = ['name', 'course__title']
    ordering = ['-start_date']
    inlines = [CohortEnrollmentInline]

    def enrollment_count(self, obj):
        return obj.enrollment_count
    enrollment_count.short_description = 'Enrolled'


@admin.register(CohortEnrollment)
class CohortEnrollmentAdmin(admin.ModelAdmin):
    """Admin for CohortEnrollment."""

    list_display = ['user', 'cohort', 'enrolled_at']
    list_filter = ['cohort__course', 'cohort']
    search_fields = ['user__email', 'cohort__name']
    raw_id_fields = ['user', 'cohort']
    readonly_fields = ['enrolled_at']
