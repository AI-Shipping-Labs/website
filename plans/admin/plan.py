"""Admin registrations for plan rows (issue #585).

Minimal registration so the staff-only "Open in Django admin" link on
the plan views resolves to a working change page. Studio remains the
primary surface for editing plans; this admin is a low-fi inspector.
"""

from django.contrib import admin

from plans.models import Plan, PlanRequest, Sprint, SprintEnrollment


@admin.register(Sprint)
class SprintAdmin(admin.ModelAdmin):
    list_display = ['name', 'slug', 'status', 'start_date', 'duration_weeks']
    list_filter = ['status']
    search_fields = ['name', 'slug']
    prepopulated_fields = {'slug': ('name',)}


@admin.register(SprintEnrollment)
class SprintEnrollmentAdmin(admin.ModelAdmin):
    list_display = ['user', 'sprint', 'enrolled_at', 'enrolled_by']
    list_filter = ['sprint']
    search_fields = ['user__email', 'sprint__name', 'sprint__slug']
    raw_id_fields = ['user', 'enrolled_by']


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'member', 'sprint', 'status', 'visibility',
        'created_at', 'updated_at',
    ]
    list_filter = ['status', 'visibility', 'sprint']
    search_fields = ['member__email', 'sprint__name', 'sprint__slug']
    raw_id_fields = ['member']
    readonly_fields = ['comment_content_id', 'created_at', 'updated_at']


@admin.register(PlanRequest)
class PlanRequestAdmin(admin.ModelAdmin):
    list_display = ['member', 'sprint', 'created_at']
    list_filter = ['sprint']
    search_fields = ['member__email', 'sprint__name', 'sprint__slug']
    raw_id_fields = ['member']
    readonly_fields = ['created_at', 'updated_at']
