from django.contrib import admin

from content.models import CourseCertificate, CourseProject, PeerReview, ProjectSubmission
from content.services.project_results import review_points


@admin.register(ProjectSubmission)
class ProjectSubmissionAdmin(admin.ModelAdmin):
    list_display = ['user', 'course', 'status', 'submitted_at']
    list_filter = ['status', 'course']
    raw_id_fields = ['user', 'course', 'cohort']
    search_fields = ['user__email', 'course__title', 'project_url']
    readonly_fields = ['submitted_at']


@admin.register(PeerReview)
class PeerReviewAdmin(admin.ModelAdmin):
    list_display = ['reviewer', 'submission', 'is_complete', 'score', 'criteria_total', 'assigned_at']
    list_filter = ['is_complete']
    raw_id_fields = ['submission', 'reviewer']
    search_fields = ['reviewer__email', 'submission__user__email']
    readonly_fields = ['assigned_at']

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            'submission__course_project',
        )

    @admin.display(description='Criteria total (pts)')
    def criteria_total(self, obj):
        points = review_points(obj)
        if points is None:
            return '-'
        return f'{points} pts'


@admin.register(CourseCertificate)
class CourseCertificateAdmin(admin.ModelAdmin):
    list_display = ['user', 'course', 'issued_at', 'id']
    raw_id_fields = ['user', 'course', 'submission']
    search_fields = ['user__email', 'course__title']
    readonly_fields = ['issued_at']


@admin.register(CourseProject)
class CourseProjectAdmin(admin.ModelAdmin):
    """``state`` is CMP's Project.state: staff move it by hand (dated attempts)."""

    list_display = ['title', 'course', 'cohort', 'state', 'submission_due_at', 'review_due_at']
    list_filter = ['state', 'course']
    search_fields = ['title', 'slug']
    raw_id_fields = ['course', 'cohort', 'module']
