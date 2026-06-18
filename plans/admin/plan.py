"""Admin registrations for plan rows (issue #585).

Minimal registration so the staff-only "Open in Django admin" link on
the plan views resolves to a working change page. Studio remains the
primary surface for editing plans; this admin is a low-fi inspector.
"""

from django.contrib import admin

from plans.models import (
    Plan,
    PlanReadyEmailLog,
    PlanRequest,
    Sprint,
    SprintEnrollment,
    SprintFeedbackRequest,
)
from studio.admin_links import studio_link


@admin.register(Sprint)
class SprintAdmin(admin.ModelAdmin):
    list_display = [
        'name', 'slug', 'status', 'start_date', 'duration_weeks',
        'studio_link',
    ]
    list_filter = ['status']
    search_fields = ['name', 'slug']
    prepopulated_fields = {'slug': ('name',)}
    readonly_fields = ['studio_link']

    @admin.display(description='Studio')
    def studio_link(self, obj):
        return studio_link(
            obj,
            'studio_sprint_detail',
            lambda o: {'sprint_id': o.pk},
        )


@admin.register(SprintEnrollment)
class SprintEnrollmentAdmin(admin.ModelAdmin):
    list_display = ['user', 'sprint', 'enrolled_at', 'enrolled_by']
    list_filter = ['sprint']
    search_fields = ['user__email', 'sprint__name', 'sprint__slug']
    raw_id_fields = ['user', 'enrolled_by']


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'member', 'sprint', 'visibility',
        'created_at', 'updated_at', 'studio_link',
    ]
    list_filter = ['visibility', 'sprint']
    search_fields = ['member__email', 'sprint__name', 'sprint__slug']
    raw_id_fields = ['member']
    readonly_fields = [
        'comment_content_id', 'created_at', 'updated_at', 'studio_link',
    ]

    @admin.display(description='Studio')
    def studio_link(self, obj):
        return studio_link(
            obj,
            'studio_plan_detail',
            lambda o: {'plan_id': o.pk},
        )


@admin.register(PlanReadyEmailLog)
class PlanReadyEmailLogAdmin(admin.ModelAdmin):
    list_display = [
        'plan', 'sprint', 'member', 'status', 'sent_at', 'triggered_by',
        'updated_at',
    ]
    list_filter = ['status', 'sprint']
    search_fields = ['member__email', 'sprint__name', 'sprint__slug']
    raw_id_fields = [
        'plan', 'sprint', 'member', 'triggered_by', 'notification',
        'email_log',
    ]
    readonly_fields = ['created_at', 'updated_at']


@admin.register(PlanRequest)
class PlanRequestAdmin(admin.ModelAdmin):
    list_display = ['member', 'sprint', 'created_at']
    list_filter = ['sprint']
    search_fields = ['member__email', 'sprint__name', 'sprint__slug']
    raw_id_fields = ['member']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(SprintFeedbackRequest)
class SprintFeedbackRequestAdmin(admin.ModelAdmin):
    list_display = [
        'sprint', 'questionnaire', 'distributed_at', 'created_by', 'created_at',
    ]
    list_filter = ['sprint']
    search_fields = ['sprint__name', 'sprint__slug', 'questionnaire__title']
    raw_id_fields = ['sprint', 'questionnaire', 'created_by']
    readonly_fields = ['created_at', 'updated_at']
