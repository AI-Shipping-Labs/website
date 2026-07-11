from django.conf import settings
from django.db import models


class EmailChangeRequest(models.Model):
    """Pending self-service login email change for a member account."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="email_change_requests",
    )
    old_email = models.EmailField()
    new_email = models.EmailField()
    token_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField(db_index=True)
    confirmed_at = models.DateTimeField(null=True, blank=True, db_index=True)
    invalidated_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_sent_at = models.DateTimeField()

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user"],
                condition=models.Q(
                    confirmed_at__isnull=True,
                    invalidated_at__isnull=True,
                ),
                name="unique_active_email_change_request_per_user",
            ),
        ]

    @property
    def is_pending(self):
        return self.confirmed_at is None and self.invalidated_at is None

    def __str__(self):
        return f"EmailChangeRequest({self.old_email} -> {self.new_email})"
