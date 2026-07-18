"""CommunityAuditLog model for tracking all community actions.

Logs every Slack API action (invite, remove, reactivate, link) with
the user, timestamp, and details.
"""

from django.conf import settings
from django.db import models


class CommunityAuditLog(models.Model):
    """Audit log entry for community (Slack) actions.

    Every invite, remove, reactivate, or email-match link action is
    recorded here for debugging and compliance.
    """

    ACTION_CHOICES = [
        ("invite", "Invite"),
        ("remove", "Remove"),
        ("reactivate", "Reactivate"),
        ("link", "Link"),
        ("check", "Check"),
        ("email_synced_from_stripe", "Email synced from Stripe"),
        # User Management API writes (issue #764). The audit row's
        # ``user`` FK stays the SUBJECT of the action (the user being
        # mutated); the actor identity (API token name) lives in
        # ``details`` as ``actor_token=<name or key_prefix>``.
        ("api_unsubscribe", "API: unsubscribe toggle"),
        ("api_verify", "API: email verification"),
        ("api_tag", "API: tag add/remove"),
        # Operator-triggered bounce mark (issue #784). Webhook-equivalent
        # side-effects (synthetic SesEvent row + bounce-state writes) run
        # through ``accounts.utils.bounce``; the audit row records the
        # attempt regardless of whether it actually changed state.
        ("api_mark_bounced", "API: mark bounced"),
        # Operator-triggered 10-year ``main`` tier override grant via
        # ``POST /api/tier-overrides`` (issue #833). The audit row's ``user``
        # FK is the SUBJECT (the granted user); the actor (API token name)
        # rides in ``details`` as ``actor_token=<label>`` alongside the tier
        # slug. Idempotent skips do NOT write a row.
        ("api_tier_override", "API: tier override grant"),
        # Operator-managed email aliases via the alias API (issue #840a).
        # The audit row's ``user`` FK is the alias OWNER (the canonical
        # account); the actor (API token name) rides in ``details`` as
        # ``actor_token=<label>`` alongside the alias email. Idempotent and
        # no-op attempts STILL write a row (the operator decision is logged).
        ("email_alias_added", "API: email alias added"),
        ("email_alias_removed", "API: email alias removed"),
        ("payment_mismatch_recorded", "Payment mismatch recorded"),
        ("payment_mismatch_updated", "Payment mismatch updated"),
        # Account merge via ``POST /api/users/merge`` (issue #841). One row per
        # real (non-dry-run) merge. The ``user`` FK is the SURVIVING canonical
        # account; ``details`` is a JSON summary of moved rows / reconciled
        # scalars / conflicts with ``actor_token=<label>``. Dry runs and the
        # already-merged no-op write NO row.
        ("merge_accounts", "API: account merge"),
        # Duplicate-event merge via the Studio tool / ``merge_duplicate_events``
        # command (issue #881). One row per real (non-dry-run) merge. There is no
        # natural user SUBJECT for an event merge, so the ``user`` FK records the
        # ACTOR (the staff operator who ran the merge); ``details`` is a JSON
        # summary with the canonical / retired event ids, the registration count
        # moved, and the fields filled, plus ``actor_token=<label>``. Dry runs and
        # the already-merged no-op write NO row.
        ("merge_events", "Studio: duplicate event merge"),
        # Maven cohort enrollment auto-onboarding (issue #960). One row per
        # override grant/refresh triggered by a ``user_cohort.enrolled``
        # webhook. The ``user`` FK is the SUBJECT (the enrolled member);
        # ``details`` records the tier slug, computed expiry, cohort, and
        # whether the override was newly granted or refreshed/extended.
        ("maven_enrollment_override", "Maven: cohort enrollment override"),
        ("maven_step_retry", "Maven: operator step retry"),
        (
            "questionnaire_response_reviewed",
            "Questionnaire response reviewed",
        ),
        (
            "questionnaire_response_reopened",
            "Questionnaire response reopened",
        ),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="community_audit_logs",
        help_text="The user this action was performed for.",
    )
    action = models.CharField(
        max_length=32,
        choices=ACTION_CHOICES,
        help_text="Type of community action performed.",
    )
    timestamp = models.DateTimeField(
        auto_now_add=True,
        help_text="When the action occurred.",
    )
    details = models.TextField(
        blank=True,
        default="",
        help_text="Additional details about the action (e.g. Slack API response, error info).",
    )

    class Meta:
        ordering = ["-timestamp"]
        verbose_name = "Community Audit Log"
        verbose_name_plural = "Community Audit Logs"

    def __str__(self):
        return f"{self.action} - {self.user.email} at {self.timestamp}"
