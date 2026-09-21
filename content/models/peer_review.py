import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, URLValidator
from django.db import models
from django.db.models import Q

SUBMISSION_STATUS_CHOICES = [
    ('submitted', 'Submitted'),
    ('in_review', 'In Review'),
    ('review_complete', 'Review Complete'),
    ('certified', 'Certified'),
]


class CourseProject(models.Model):
    """A dated project attempt within a course, optionally scoped to a cohort."""

    course = models.ForeignKey(
        'cb_curriculum.Course', on_delete=models.CASCADE,
        related_name='course_projects',
    )
    cohort = models.ForeignKey(
        'content.Cohort', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='course_projects',
    )
    module = models.ForeignKey(
        'cb_curriculum.Module', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='course_projects',
    )
    slug = models.SlugField(max_length=100)
    title = models.CharField(max_length=200)
    submission_due_at = models.DateTimeField()
    review_due_at = models.DateTimeField()
    peer_review_count = models.PositiveSmallIntegerField(
        null=True, blank=True,
        validators=[MinValueValidator(1)],
        help_text='Reviews per learner; blank uses the course setting.',
    )

    class Meta:
        ordering = ['submission_due_at', 'pk']
        constraints = [
            models.UniqueConstraint(
                fields=['course', 'slug'],
                name='course_project_course_slug_unique',
            ),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.cohort_id and self.course_id and self.cohort.course_id != self.course_id:
            errors['cohort'] = 'The cohort must belong to this course.'
        if self.module_id and self.course_id and self.module.course_id != self.course_id:
            errors['module'] = 'The module must belong to this course.'
        if self.submission_due_at and self.review_due_at:
            if self.review_due_at <= self.submission_due_at:
                errors['review_due_at'] = 'Review deadline must follow submission deadline.'
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f'{self.course.title} - {self.title}'


class ProjectSubmission(models.Model):
    """A student's submitted project for a course."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='project_submissions',
    )
    course = models.ForeignKey(
        'cb_curriculum.Course',
        on_delete=models.CASCADE,
        related_name='project_submissions',
    )
    course_project = models.ForeignKey(
        CourseProject,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='submissions',
    )
    cohort = models.ForeignKey(
        'content.Cohort',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='project_submissions',
        help_text="Set if student is in a cohort; null for self-paced.",
    )
    project_url = models.URLField(max_length=500)
    description = models.TextField(blank=True, default='')
    status = models.CharField(
        max_length=20,
        choices=SUBMISSION_STATUS_CHOICES,
        default='submitted',
    )
    submitted_at = models.DateTimeField(auto_now_add=True)
    batch_assigned_at = models.DateTimeField(null=True, blank=True)
    review_deadline = models.DateTimeField(null=True, blank=True)
    certificate_issued_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'course'],
                condition=Q(course_project__isnull=True),
                name='legacy_submission_user_course_unique',
            ),
            models.UniqueConstraint(
                fields=['user', 'course_project'],
                name='submission_user_course_project_unique',
            ),
        ]
        ordering = ['-submitted_at']

    def clean(self):
        super().clean()
        errors = {}
        if self.course_project_id:
            if self.course_id and self.course_project.course_id != self.course_id:
                errors['course_project'] = 'The project attempt must belong to this course.'
            if (self.course_project.cohort_id
                    and self.course_project.cohort_id != self.cohort_id):
                errors['cohort'] = 'The cohort must match the project attempt.'
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f'{self.user} - {self.course.title} ({self.status})'


class PeerReview(models.Model):
    """One student's review of another student's submission."""

    submission = models.ForeignKey(
        ProjectSubmission,
        on_delete=models.CASCADE,
        related_name='reviews',
    )
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='peer_reviews_given',
    )
    score = models.IntegerField(
        null=True, blank=True,
        help_text="Numeric score from 1 to 5.",
    )
    feedback = models.TextField(blank=True, default='')
    is_complete = models.BooleanField(default=False)
    assigned_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = [('submission', 'reviewer')]
        ordering = ['-assigned_at']

    def __str__(self):
        status = 'complete' if self.is_complete else 'pending'
        return f'Review by {self.reviewer} on {self.submission} ({status})'


class CourseCertificate(models.Model):
    """Certificate of completion for a course."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='course_certificates',
    )
    course = models.ForeignKey(
        'cb_curriculum.Course',
        on_delete=models.CASCADE,
        related_name='certificates',
    )
    issued_at = models.DateTimeField(auto_now_add=True)
    submission = models.ForeignKey(
        ProjectSubmission,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='certificates',
    )
    pdf_url = models.URLField(
        blank=True,
        default='',
        max_length=500,
        validators=[URLValidator(schemes=['http', 'https'])],
        help_text=(
            'Optional external URL to a PDF version of the certificate. '
            'Surfaced as a Download PDF button on the public cert page.'
        ),
    )
    # Soft-revoke fields (issue #949). A revoked certificate is retained
    # (the public /certificates/<uuid> page stays reachable and renders a
    # "revoked" state) so shared links never 404 and the audit trail is
    # preserved. Revocation is reversible by clearing all three fields.
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
        help_text='Staff user who revoked this certificate.',
    )
    revoked_reason = models.CharField(max_length=200, blank=True, default='')

    class Meta:
        unique_together = [('user', 'course')]

    def __str__(self):
        return f'Certificate: {self.user} - {self.course.title}'

    def get_absolute_url(self):
        return f'/certificates/{self.id}'

    @property
    def is_revoked(self):
        """True when this certificate has been revoked."""
        return self.revoked_at is not None
