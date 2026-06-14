"""Idempotency ledger for processed Maven cohort webhook events (issue #960).

One row is written per terminally-successful Maven webhook delivery. The
``dedupe_key`` is derived from the normalized email + cohort identifier +
event type, so a Maven/Zapier retry or a duplicate delivery for the same
enrollment collapses to the same key and the webhook short-circuits with
``already_processed`` — no second account, override, Slack invite, welcome
email, or staff removal notification.

The row is written ONLY after the work reaches a terminal success (mirrors
the Stripe-webhook ordering discipline in ``payments/views/webhooks.py``):
a transient failure returns ``500`` and writes no row, so the sender
retries and the side effects run exactly once.
"""

from django.db import models


class MavenEnrollmentEvent(models.Model):
    """Dedupe record for a processed Maven cohort webhook event."""

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
    email = models.EmailField(blank=True, default="")
    course = models.CharField(max_length=255, blank=True, default="")
    cohort = models.CharField(max_length=255, blank=True, default="")
    event_type = models.CharField(max_length=100, blank=True, default="")
    outcome = models.CharField(
        max_length=32,
        choices=OUTCOME_CHOICES,
        blank=True,
        default="",
        help_text="Terminal outcome recorded for this event.",
    )
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Maven Enrollment Event"
        verbose_name_plural = "Maven Enrollment Events"

    def __str__(self):
        return f"{self.event_type} {self.email} ({self.outcome})"
