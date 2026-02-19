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
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="community_audit_logs",
        help_text="The user this action was performed for.",
    )
    action = models.CharField(
        max_length=20,
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
