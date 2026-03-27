from django.contrib import admin

from content.models import CourseCertificate, PeerReview, ProjectSubmission


@admin.register(ProjectSubmission)
class ProjectSubmissionAdmin(admin.ModelAdmin):
    list_display = ['user', 'course', 'status', 'submitted_at']
    list_filter = ['status', 'course']
    raw_id_fields = ['user', 'course', 'cohort']
    search_fields = ['user__email', 'course__title', 'project_url']
    readonly_fields = ['submitted_at']


@admin.register(PeerReview)
class PeerReviewAdmin(admin.ModelAdmin):
    list_display = ['reviewer', 'submission', 'is_complete', 'score', 'assigned_at']
    list_filter = ['is_complete']
    raw_id_fields = ['submission', 'reviewer']
    search_fields = ['reviewer__email', 'submission__user__email']
    readonly_fields = ['assigned_at']


@admin.register(CourseCertificate)
class CourseCertificateAdmin(admin.ModelAdmin):
    list_display = ['user', 'course', 'issued_at', 'id']
    raw_id_fields = ['user', 'course', 'submission']
    search_fields = ['user__email', 'course__title']
    readonly_fields = ['issued_at']
