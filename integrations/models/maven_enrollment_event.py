"""Occurrence and per-step delivery ledger for Maven enrollment webhooks."""

from django.conf import settings
from django.db import models
from django.db.models import Q


class MavenEnrollmentEvent(models.Model):
    """One enrollment occurrence, closed by removal and reusable on re-enrolment."""

    LIFECYCLE_ACTIVE = "active"
    LIFECYCLE_REMOVED = "removed"
    LIFECYCLE_LEGACY = "legacy"
    LIFECYCLE_CHOICES = [
        (LIFECYCLE_ACTIVE, "Active"),
        (LIFECYCLE_REMOVED, "Removed"),
        (LIFECYCLE_LEGACY, "Legacy"),
    ]

    STEP_PENDING = "pending"
    STEP_RUNNING = "running"
    STEP_SUCCEEDED = "succeeded"
    STEP_FAILED = "failed"
    STEP_SKIPPED = "skipped"
    STEP_CHOICES = [
        (STEP_PENDING, "Pending"),
        (STEP_RUNNING, "Running"),
        (STEP_SUCCEEDED, "Succeeded"),
        (STEP_FAILED, "Failed"),
        (STEP_SKIPPED, "Skipped"),
    ]

    OUTCOME_ONBOARDED = "onboarded"
    OUTCOME_REFRESHED = "refreshed"
    OUTCOME_ALREADY_MEMBER = "already_member"
    OUTCOME_REMOVAL_NOTIFIED = "removal_notified"
    OUTCOME_IGNORED = "ignored"
    OUTCOME_CHOICES = [
        (OUTCOME_ONBOARDED, "Onboarded"),
        (OUTCOME_REFRESHED, "Refreshed"),
        (OUTCOME_ALREADY_MEMBER, "Already a member"),
        (OUTCOME_REMOVAL_NOTIFIED, "Removal notified"),
        (OUTCOME_IGNORED, "Ignored"),
    ]

    dedupe_key = models.CharField(
        max_length=255,
        unique=True,
        help_text="Normalized email + cohort + event type. Unique per processed event.",
    )
    identity_hash = models.CharField(max_length=64, blank=True, default="", db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="maven_enrollment_occurrences",
    )
    email = models.EmailField(blank=True, default="")
    course = models.CharField(max_length=255, blank=True, default="")
    cohort = models.CharField(max_length=255, blank=True, default="")
    course_key = models.CharField(max_length=255, blank=True, default="")
    cohort_key = models.CharField(max_length=255, blank=True, default="")
    event_type = models.CharField(max_length=100, blank=True, default="")
    outcome = models.CharField(
        max_length=32,
        choices=OUTCOME_CHOICES,
        blank=True,
        default="",
        help_text="Terminal outcome recorded for this event.",
    )
    payload = models.JSONField(default=dict, blank=True)
    payload_redacted_at = models.DateTimeField(null=True, blank=True)
    lifecycle = models.CharField(
        max_length=16,
        choices=LIFECYCLE_CHOICES,
        default=LIFECYCLE_ACTIVE,
        db_index=True,
    )
    welcome_eligible = models.BooleanField(default=False)
    override_status = models.CharField(max_length=16, choices=STEP_CHOICES, default=STEP_PENDING)
    override_attempts = models.PositiveSmallIntegerField(default=0)
    override_attempted_at = models.DateTimeField(null=True, blank=True)
    override_completed_at = models.DateTimeField(null=True, blank=True)
    override_error = models.CharField(max_length=255, blank=True, default="")
    slack_status = models.CharField(max_length=16, choices=STEP_CHOICES, default=STEP_PENDING)
    slack_attempts = models.PositiveSmallIntegerField(default=0)
    slack_attempted_at = models.DateTimeField(null=True, blank=True)
    slack_completed_at = models.DateTimeField(null=True, blank=True)
    slack_error = models.CharField(max_length=255, blank=True, default="")
    welcome_status = models.CharField(max_length=16, choices=STEP_CHOICES, default=STEP_PENDING)
    welcome_attempts = models.PositiveSmallIntegerField(default=0)
    welcome_attempted_at = models.DateTimeField(null=True, blank=True)
    welcome_completed_at = models.DateTimeField(null=True, blank=True)
    welcome_error = models.CharField(max_length=255, blank=True, default="")
    removal_status = models.CharField(max_length=16, choices=STEP_CHOICES, default=STEP_SKIPPED)
    removal_attempts = models.PositiveSmallIntegerField(default=0)
    removal_attempted_at = models.DateTimeField(null=True, blank=True)
    removal_completed_at = models.DateTimeField(null=True, blank=True)
    removal_error = models.CharField(max_length=255, blank=True, default="")
    removed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Maven Enrollment Event"
        verbose_name_plural = "Maven Enrollment Events"
        constraints = [
            models.UniqueConstraint(
                fields=["identity_hash"],
                condition=Q(lifecycle="active"),
                name="uniq_active_maven_occurrence",
            ),
        ]

    def __str__(self):
        return f"{self.event_type} {self.email} ({self.outcome})"
