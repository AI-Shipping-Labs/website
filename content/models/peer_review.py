import uuid

from django.conf import settings
from django.core.validators import URLValidator
from django.db import models

SUBMISSION_STATUS_CHOICES = [
    ('submitted', 'Submitted'),
    ('in_review', 'In Review'),
    ('review_complete', 'Review Complete'),
    ('certified', 'Certified'),
]


class ProjectSubmission(models.Model):
    """A student's submitted project for a course."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='project_submissions',
    )
    course = models.ForeignKey(
        'content.Course',
        on_delete=models.CASCADE,
        related_name='project_submissions',
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
        unique_together = [('user', 'course')]
        ordering = ['-submitted_at']

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
        'content.Course',
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

    class Meta:
        unique_together = [('user', 'course')]

    def __str__(self):
        return f'Certificate: {self.user} - {self.course.title}'

    def get_absolute_url(self):
        return f'/certificates/{self.id}'
